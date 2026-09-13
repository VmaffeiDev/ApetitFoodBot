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
const telas = JSON.parse(fs.readFileSync(path.join(root, 'demo/telas.json'), 'utf8'));
const regras = JSON.parse(fs.readFileSync(path.join(root, 'demo/regras.json'), 'utf8'));
let scenarios = 0;
const base = {nome:'Teste', refeitorio:'SM', empresa:'Teste', setor:'TI', consentimento:true};

async function app(profile) {
  const errors = [], console = new VirtualConsole();
  console.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(source, {runScripts:'dangerously', url:'https://apetit.test', virtualConsole:console,
    beforeParse(w) {
      w.__APETIT__ = {dados: structuredClone(data), telas: structuredClone(telas), regras};
      w.matchMedia = () => ({matches:true});
      w.HTMLDialogElement.prototype.showModal = function(){this.setAttribute('open','');};
      w.HTMLDialogElement.prototype.close = function(){this.removeAttribute('open');};
      if (profile) w.localStorage.setItem('apetit-cadastro', JSON.stringify(profile));
    }});
  await new Promise(resolve => setImmediate(resolve));
  const d = dom.window.document;
  const button = label => {
    const b = [...d.querySelectorAll('button')].find(b => b.textContent.trim() === label || b.querySelector('b')?.textContent === label);
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
      // Percorre todas as categorias; bloqueados não podem ser escolhidos.
      for (let step=0; step<data.cardapio.length; step++) {
        for (const code of data.cardapio[step].itens.map(i => i.codigo)) {
          const b = a.d.querySelector(`.escolha-prato[data-codigo="${code}"]`);
          assert.equal(b.dataset.veredito, restrictions.vereditos[code]);
          assert.equal(b.disabled, blocked.includes(code));
          b.click();
          const updated = a.d.querySelector(`.escolha-prato[data-codigo="${code}"]`);
          assert.equal(updated.getAttribute('aria-pressed'), blocked.includes(code) ? 'false' : 'true');
        }
        if (step<data.cardapio.length-1) a.click('Próxima categoria');
      }
      const selected = data.cardapio.flatMap(g => g.itens).map(i => i.codigo).filter(c => !blocked.includes(c)).sort();
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
      b.tab('inicio'); b.click('Quanto pegar hoje');
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
  console.log(`${scenarios} cenários passaram: montagem, restrições, registro, histórico, pontos e três períodos do gráfico (DOM).`);
}
main().catch(error => {console.error(error); process.exitCode=1;});
