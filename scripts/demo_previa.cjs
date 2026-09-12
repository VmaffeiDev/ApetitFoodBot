/* Mantem a home visivel em leitores de HTML que nao executam JavaScript.
 * Executa a interface existente, captura somente os tres containers da home
 * e os inclui no documento. Num navegador, o app reassume esses containers.
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
    }
  });
  try {
    await new Promise(resolve => setImmediate(resolve));
    const document = dom.window.document;
    if (errors.length) throw new Error(errors.join('\n'));
    if (!document.querySelector('.hero-refeicao')) throw new Error('A home nao carregou.');
    let output = html;
    for (const [tag, id] of [['header', 'topo'], ['main', 'tela'], ['nav', 'abas']]) {
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
    process.stdout.write('Previa pronta: home visivel com ou sem JavaScript.\n');
  } finally {
    dom.window.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
