/* Regressões dos fluxos no DOM, sem renderização de navegador.
 * Uso: npm ci --prefix scripts && node scripts/conferir_fluxos.cjs
 * Confere o código do app com os resultados exportados pelo domínio Python.
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'demo/index.html'), 'utf8');
const data = JSON.parse(fs.readFileSync(path.join(root, 'demo/dados.json'), 'utf8'));
const regras = JSON.parse(fs.readFileSync(path.join(root, 'demo/regras.json'), 'utf8'));
let scenarios = 0;
const base = {nome:'Teste', refeitorio:'SM', empresa:'Teste', setor:'TI', consentimento:true};

async function app(profile) {
  const errors = [], console = new VirtualConsole();
  console.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(source, {runScripts:'dangerously', url:'https://apetit.test', virtualConsole:console,
    beforeParse(w) {
      w.__APETIT__ = {dados: structuredClone(data), regras};
      w.matchMedia = () => ({matches:true});
      w.HTMLDialogElement.prototype.showModal = function(){this.setAttribute('open','');};
      w.HTMLDialogElement.prototype.close = function(){this.removeAttribute('open');};
      if (profile) w.localStorage.setItem('apetit-cadastro', JSON.stringify(profile));
    }});
  await new Promise(resolve => setImmediate(resolve));
  const d = dom.window.document;
  const button = label => {
    const b = [...d.querySelectorAll('button')].find(b => b.textContent.trim() === label || b.querySelector('b')?.textContent === label || b.getAttribute('aria-label') === label);
    assert.ok(b, `Botão ausente: ${label}`); return b;
  };
  const click = label => {const b=button(label); assert.ok(!b.disabled, label); b.click();};
  if (!profile) click('Só olhar, com o exemplo da Mariana');
  return {d, dom, click, button, tab:n => d.querySelector(`[data-aba="${n}"]`).click(),
    text:() => d.querySelector('#tela').textContent,
    close:() => {dom.window.close(); assert.deepEqual(errors, []);}};
}

function checkRecorded(a, expected, prediction, prior = 0) {
  a.tab('progresso');
  for (const period of ['Semana','Mês','Ano']) {
    a.click(period);
    const total = [...a.d.querySelectorAll('.valor-dia')].reduce((sum,n) => sum + (Number(n.textContent) || 0), 0);
    assert.equal(total, expected.kcal + prior, `Total do gráfico ${period}`);
  }
  assert.equal(Number(a.d.querySelector('.pontos .n').textContent), prediction.pontos_depois);
  a.tab('inicio');
  assert.ok(a.d.querySelector('.refeicao-home').textContent.includes(expected.kcal + ' kcal'));
  assert.ok(a.d.querySelector('.home-acoes .cta.forte').textContent.includes('Avaliar o almoço'));
  if (!prior) assert.equal(a.d.querySelectorAll('.conq-item').length, prediction.conquistas.length);
  a.tab('perfil'); a.click('Meu dia');
  assert.ok(a.text().includes('Almoço de hoje registrado.'));
  assert.ok(!a.text().includes('Nada registrado ainda.'));
  for (const i of expected.itens) assert.ok(a.text().includes(i.nome));
  a.tab('perfil'); a.click('Meus dados e privacidade');
  const fields = [...a.d.querySelectorAll('.campo')];
  const value = name => fields.find(n => n.querySelector('dt').textContent === name).querySelector('dd').textContent;
  assert.equal(Number(value('Dias com refeição registrada')), prior ? data.meu_dia.dias.length + 1 : 1);
  assert.equal(Number(value('Pontos')), prediction.pontos_depois);
}

async function main() {
  // Cada conjunto de alergias e objetivo: não só o exemplo da Mariana.
  for (const restrictions of data.conformidade) for (const objective of data.objetivos) {
    const profile = {...base, restricoes:restrictions.restricoes, objetivo:objective.nome};
    const blocked = Object.keys(restrictions.vereditos).filter(c => restrictions.vereditos[c] === 'bloqueio').sort();
    const combination = data.combinacoes.find(c => c.objetivo === objective.nome && c.bloqueados.slice().sort().join('|') === blocked.join('|'));
    const a = await app(profile);
    try {
      assert.equal(a.d.querySelector('.pontos .n').textContent, '0');
      a.tab('progresso');
      for (const period of ['Semana','Mês','Ano']) {a.click(period); assert.ok([...a.d.querySelectorAll('.valor-dia')].every(n => n.textContent === '—'));}
      a.tab('inicio'); a.click('Montar meu prato');
      const selected = [];
      // Percorre todas as categorias; bloqueados não podem ser escolhidos.
      for (let step=0; step<data.cardapio.length; step++) {
        for (const code of data.cardapio[step].itens.map(i => i.codigo)) {
          const b = a.d.querySelector(`.escolha-prato[data-codigo="${code}"]`);
          assert.equal(b.dataset.veredito, restrictions.vereditos[code]);
          assert.equal(b.disabled, blocked.includes(code));
          b.click();
          const updated = a.d.querySelector(`.escolha-prato[data-codigo="${code}"]`);
          assert.equal(updated.getAttribute('aria-pressed'), blocked.includes(code) ? 'false' : 'true');
          if (!blocked.includes(code)) {
            let quantity = 1;
            const option = data.opcoes_montagem.find(o=>o.codigo===code);
            if (objective === data.objetivos[data.objetivos.length-1]) {
              while (quantity < option.maximo) {
                a.d.querySelector(`[data-quantidade-codigo="${code}"] [data-ajuste="mais"]`).click();
                quantity++;
              }
              if (option.maximo > 1) assert.ok(a.d.querySelector(`[data-quantidade-codigo="${code}"] [data-ajuste="mais"]`).disabled);
            }
            selected.push(...Array(quantity).fill(code));
          }
        }
        if (step<data.cardapio.length-1) a.click('Próxima categoria');
      }
      selected.sort();
      const manual = data.montagens.find(m => m.codigos.join('|') === selected.join('|'));
      const prediction = manual.objetivos.find(o => o.objetivo === objective.nome).novo;
      a.click('Revisar meu prato'); a.click('Registrar este prato');
      assert.ok(a.text().includes('não chega à Apetit'));
      checkRecorded(a, manual.refeicao, prediction);
      // Mudar o perfil após registro não modifica os totais históricos.
      a.dom.window.localStorage.setItem('apetit-cadastro', JSON.stringify({...profile, objetivo:data.objetivos[0].nome}));
      checkRecorded(a, manual.refeicao, prediction);
      scenarios++;
    } finally {a.close();}
    const b = await app(profile);
    try {
      b.click('Quanto pegar hoje');
      const codes = [...b.d.querySelectorAll('.linha-porcao')].map(n => n.dataset.codigo);
      assert.ok(codes.every(c => !blocked.includes(c)));
      b.click('Vou pegar isso — registrar');
      checkRecorded(b, combination.registro.refeicao, combination.registro);
      b.tab('inicio'); b.click('Ver refeição registrada');
      assert.equal([...b.d.querySelectorAll('button')].filter(n => n.textContent.includes('Vou pegar isso')).length, 0);
      scenarios++;
    } finally {b.close();}
  }
  // Restrição alterada entre a revisão e o clique final: validação no registro.
  const a = await app({...base, restricoes:[], objetivo:'Comer mais leve'});
  try {
    a.click('Montar meu prato'); a.click('Próxima categoria'); a.click('Próxima categoria');
    a.d.querySelector('.escolha-prato').click(); a.click('Revisar meu prato');
    a.dom.window.localStorage.setItem('apetit-cadastro', JSON.stringify({...base, restricoes:['gluten'], objetivo:'Comer mais leve'}));
    a.click('Registrar este prato');
    assert.equal(a.d.querySelector('.escolha-prato').getAttribute('aria-pressed'), 'false');
    assert.ok(a.d.querySelector('.escolha-prato').disabled);
    a.tab('inicio'); assert.equal(a.d.querySelector('.pontos .n').textContent, '0');
    scenarios++;
  } finally {a.close();}
  // O exemplo continua com o histórico dele, acrescido do registro atual.
  const b = await app(null);
  try {
    b.click('Quanto pegar hoje'); b.click('Vou pegar isso — registrar');
    checkRecorded(b, data.registro_previsto.refeicao, data.registro_previsto, data.meu_dia.dias.reduce((s,d)=>s+d.kcal,0));
    scenarios++;
  } finally {b.close();}
  // Quantidades na revisão, edição direta com cancelamento e registro imutável.
  const c = await app({...base, restricoes:[], objetivo:'Comer mais leve', aceite:'2026-09-01T12:00:00Z'});
  try {
    const saved=()=>JSON.parse(c.dom.window.localStorage.getItem('apetit-cadastro'));
    const edit=field=>{c.tab('perfil'); c.d.querySelector(`[data-editar-campo="${field}"]`).click();};
    const fill=(field,value)=>{const input=c.d.getElementById('campo-'+field); input.value=value; input.dispatchEvent(new c.dom.window.Event('input',{bubbles:true}));};
    const original=saved();
    edit('objetivo'); c.click('Reforcar a proteina'); c.click('Cancelar'); assert.deepEqual(saved(),original);
    for (const [field,value] of [['nome','Nome novo'],['empresa','Empresa nova'],['setor','Setor novo'],['refeitorio','Unidade nova']]) {
      const before=saved(); edit(field);
      assert.equal(c.d.querySelectorAll('#tela input').length,1);
      fill(field,'   '); assert.ok(c.button('Salvar alterações').disabled);
      fill(field,value); c.click('Salvar alterações');
      assert.deepEqual(saved(),{...before,[field]:value});
    }
    edit('objetivo'); c.click('Manter o equilibrio'); c.click('Salvar alterações'); assert.equal(saved().objetivo,'Manter o equilibrio');
    edit('objetivo'); c.click('Comer mais leve'); c.click('Salvar alterações');
    edit('restricoes'); c.click('Ovos'); c.click('Salvar alterações'); assert.deepEqual(saved().restricoes,['ovos']);
    assert.equal(saved().aceite,original.aceite);
    c.tab('cardapio'); assert.equal(c.d.querySelector('[data-codigo="ovo_cozido"]').dataset.veredito,'bloqueio');
    c.tab('inicio'); c.click('Montar meu prato'); c.d.querySelector('.escolha-prato').click();
    c.click('Aumentar quantidade de Carne assada ao molho');
    for(let n=0;n<3;n++)c.click('Próxima categoria');
    c.d.querySelector('.escolha-prato').click(); c.click('Revisar meu prato');
    c.click('Aumentar quantidade de Arroz parboilizado');
    c.click('Aumentar quantidade de Arroz parboilizado');
    assert.ok(c.button('Aumentar quantidade de Arroz parboilizado').disabled);
    c.click('Diminuir quantidade de Arroz parboilizado');
    c.click('Diminuir quantidade de Arroz parboilizado');
    assert.ok(c.button('Diminuir quantidade de Arroz parboilizado').disabled);
    assert.ok(c.text().includes('406 kcal'));
    const meal=data.montagens.find(m=>m.codigos.join('|')===['arroz_parboilizado','carne_assada_ao_molho','carne_assada_ao_molho'].join('|'));
    const prediction=meal.objetivos.find(o=>o.objetivo==='Comer mais leve').novo;
    c.click('Registrar este prato'); checkRecorded(c,meal.refeicao,prediction);
    edit('objetivo'); c.click('Reforcar a proteina'); c.click('Salvar alterações'); checkRecorded(c,meal.refeicao,prediction);
    c.tab('inicio'); c.click('Avaliar o almoço'); c.click('Boa'); c.click('Enviar avaliação');
    c.tab('inicio'); assert.ok(c.button('Ver minha avaliação'));
    scenarios++;
  } finally {c.close();}
  console.log(`${scenarios} cenários passaram: montagem, restrições, registro, histórico, pontos e três períodos do gráfico (DOM).`);
}
main().catch(error => {console.error(error); process.exitCode=1;});
