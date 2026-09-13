/* Mantem a primeira tela visivel em leitores de HTML que nao executam
 * JavaScript. Executa a interface existente, captura somente os tres
 * containers e os inclui no documento. Num navegador, o app reassume esses
 * containers.
 *
 * A primeira tela agora e a de boas-vindas: quem abre o app pela primeira vez
 * se cadastra antes de ver o resto. O leitor estatico ve exatamente isso — o
 * snapshot nao pode mostrar uma home que o navegador nao vai abrir.
 *
 * Uso: node scripts/demo_previa.cjs preview/Apetit-previa-visual.html
 */
const fs = require('node:fs');
const { JSDOM, VirtualConsole } = require('jsdom');

async function main() {
  const filename = process.argv[2];
  if (!filename) throw new Error('Informe o HTML gerado com demo_pagina.py --documento.');
  const html = fs.readFileSync(filename, 'utf8');
  const errors = [];
  const console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, {
    url: 'https://preview.apetit.invalid/',
    runScripts: 'dangerously',
    // Recursos externos nao sao carregados. Usamos somente o codigo local.
    virtualConsole: console,
    beforeParse(window) {
      window.matchMedia = () => ({ matches: true });
      window.HTMLDialogElement.prototype.showModal = function() { this.setAttribute('open', ''); };
      window.HTMLDialogElement.prototype.close = function() { this.removeAttribute('open'); };
    }
  });
  try {
    await new Promise(resolve => setImmediate(resolve));
    const document = dom.window.document;
    if (errors.length) throw new Error(errors.join('\n'));
    if (!document.querySelector('.acoes .cta')) throw new Error('A tela de boas-vindas nao carregou.');
    let output = html;
    for (const [tag, id] of [['header', 'topo'], ['main', 'tela'], ['div', 'acoes'], ['nav', 'abas']]) {
      const pattern = new RegExp(`<${tag}\\b[^>]*\\bid="${id}"[^>]*>[\\s\\S]*?</${tag}>`);
      if (!pattern.test(output)) throw new Error(`Container ausente: ${id}`);
      const element = document.getElementById(id);
      element.classList.remove('entrando');
      output = output.replace(pattern, () => element.outerHTML);
    }
    // A animacao de entrada nao deve esconder o snapshot num leitor estatico.
    output = output.replace('Prévia interativa · Cardápio e perfil de exemplo',
      'Prévia visual · Cardápio e perfil de exemplo');
    fs.writeFileSync(filename, output);
    if (process.argv[3]) {
      const shots = [];
      const go = name => document.querySelector(`[data-aba="${name}"]`).click();
      const click = (label, scope = document) => {
        const button = [...scope.querySelectorAll('button')].find(b => b.textContent.trim() === label || b.querySelector('b')?.textContent === label || b.getAttribute('aria-label') === label);
        if (!button || button.disabled) throw new Error(`Acao indisponivel: ${label}`);
        button.click();
      };
      const capture = label => {
        const clone = document.querySelector('.app').cloneNode(true);
        clone.querySelector('#faixa-instalar').remove();
        clone.querySelector('#arquivo-pdf').remove();
        clone.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
        clone.querySelector('.tela').classList.remove('entrando');
        clone.querySelector('.tela').removeAttribute('tabindex');
        shots.push(`<article class="preview-page"><h2 class="preview-label">${label}</h2>${clone.outerHTML}</article>`);
      };
      // A galeria percorre o app como uma pessoa nova: ela se cadastra e so
      // depois chega na home. Assim as telas capturadas sao as de alguem que
      // existe, e nao as de um exemplo que ninguem preencheu.
      const fill = (id, value) => {
        const input = document.getElementById(id);
        if (!input) throw new Error(`Campo ausente: ${id}`);
        input.value = value;
        input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
      };
      capture('01. Boas-vindas');
      click('Criar meu cadastro');
      fill('campo-nome', 'Mariana'); click('Continuar');
      fill('campo-refeitorio', 'SM'); click('Continuar');
      fill('campo-empresa', 'Indústria Exemplo'); fill('campo-setor', 'Produção');
      capture('02. Cadastro · empresa e setor');
      click('Continuar');
      click('Manter o equilibrio'); click('Continuar');
      click('Ovos'); click('Leite e derivados');
      capture('03. Cadastro · alergias');
      click('Continuar');
      click('Li e concordo'); capture('04. Cadastro · o que fica guardado');
      click('Criar meu cadastro');
      capture('05. Início');
      go('cardapio'); capture('06. Cardápio');
      go('inicio'); click('Montar meu prato');
      document.querySelector('.escolha-prato:not(:disabled)').click();
      click('Aumentar quantidade de Carne assada ao molho');
      capture('07. Montar meu prato');
      click('Revisar meu prato'); capture('08. Revisar a montagem');
      go('inicio'); click('Quanto pegar hoje'); capture('09. Quanto pegar hoje');
      click('Vou pegar isso — registrar');
      go('inicio'); capture('10. Início · almoço registrado');
      click('Avaliar o almoço');
      click('Boa'); click('Comida fria'); click('Enviar avaliação'); capture('11. Avaliar o refeitório');
      go('progresso'); capture('12. Progresso');
      click('Ver meu histórico'); capture('13. Meu dia');
      go('perfil'); capture('14. Perfil');
      click('Editar objetivo'); capture('15. Editar objetivo'); click('Cancelar');
      click('Editar restrições'); capture('16. Editar restrições'); click('Cancelar');
      click('Sou da gestão da Apetit'); click('Visão da Apetit'); capture('17. Gestão');
      click('Sobre esta demonstração', document.querySelector('#topo'));
      click('Ver estados da interface', document.querySelector('dialog')); capture('18. Estados da interface');
      if (errors.length) throw new Error(errors.join('\n'));
      const style = document.querySelector('style').textContent;
      const symbols = document.querySelector('body > svg').outerHTML;
      const css = `
        body{height:auto;background:#090B0D;padding:24px 16px 40px}
        .preview-heading{max-width:1660px;margin:0 auto 28px}
        .preview-heading h1{font-size:28px;margin-bottom:8px}
        .preview-heading p{font-size:13px;color:var(--texto-2);max-width:70ch}
        .preview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));align-items:start;gap:28px 20px;max-width:1660px;margin:auto}
        .preview-page{min-width:0}
        .preview-label{font-size:14px;color:var(--texto-2);margin:0 0 12px 8px}
        .preview-page .app{height:auto;max-height:none;min-height:800px;max-width:440px;width:100%;margin:0 auto;border:1px solid #33383E;border-radius:26px;overflow:hidden;box-shadow:0 12px 28px #0006}
        .preview-page .tela{flex:1;overflow:visible;max-height:none}
        .preview-page .acoes{max-height:none;overflow:visible}
        .preview-page button{pointer-events:none}
        .preview-page .estado-exemplo.loading .icone{animation:none}
        @media(max-width:440px){body{padding:18px 10px}.preview-heading h1{font-size:24px}.preview-page .app{min-height:0}.preview-grid{gap:26px}}
      `;
      const gallery = `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Apetit · As telas do app</title><style>${style}\n${css}</style></head><body>${symbols}<div class="preview-heading"><h1>Apetit · Novo visual</h1><p>As telas do app, do cadastro ao relatório, com os dados da demonstração. Role para ver todas. Esta galeria é estática; as fotos de comida são ilustrativas.</p></div><div class="preview-grid">${shots.join('\n')}</div></body></html>`;
      fs.writeFileSync(process.argv[3], gallery.replace(/[ \t]+$/gm, ''));
    }
    process.stdout.write('Previa pronta: primeira tela visivel com ou sem JavaScript.\n');
  } finally {
    dom.window.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
