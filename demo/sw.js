/* Service worker da demonstracao.
 *
 * Existe por dois motivos. O primeiro e que sem ele o navegador nao oferece
 * instalar na tela inicial. O segundo importa mais para quem vai testar: o
 * refeitorio costuma ter sinal ruim, e um "app" que abre em branco no
 * subsolo da fabrica nao demonstra nada. Com o shell em cache, abre sempre.
 *
 * A estrategia e cache-first para os arquivos do app e network-first para os
 * dados das telas, para uma republicacao chegar sem a pessoa reinstalar.
 */

// Subir a versao e o que faz o `index.html` novo chegar em quem ja instalou: o
// shell e servido do cache, entao sem trocar a chave o testador continuaria com
// a tela antiga por tempo indeterminado.
const VERSAO = "apetit-demo-v7";
const SHELL = [
  ".",
  "index.html",
  "manifest.webmanifest",
  "icon-192.png",
  "icon-512.png",
  "icon-maskable-512.png",
  "apple-touch-icon.png",
  "assets/pratos.webp",
];

self.addEventListener("install", (evento) => {
  evento.waitUntil(
    caches.open(VERSAO)
      // addAll falha inteiro se um arquivo faltar; aqui cada um e opcional,
      // para um icone ausente nao impedir a instalacao do resto.
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((chaves) => Promise.all(
        chaves.filter((c) => c !== VERSAO).map((c) => caches.delete(c))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (evento) => {
  const requisicao = evento.request;
  if (requisicao.method !== "GET") return;

  const url = new URL(requisicao.url);
  // O cardapio do dia vem do servidor da Apetit, que e outra origem. Deixar
  // passar sem tocar nao e so simplicidade: guardar essa resposta em cache
  // faria o app servir o cardapio de ontem achando que e o de hoje, que e
  // exatamente o que o aviso de data existe para impedir.
  if (url.origin !== self.location.origin) return;

  // Os dados mudam a cada geracao: rede primeiro, cache como rede de seguranca.
  if (/\/(telas|dados|regras)\.json$/.test(url.pathname)) {
    evento.respondWith(
      fetch(requisicao)
        .then((resposta) => {
          const copia = resposta.clone();
          caches.open(VERSAO).then((cache) => cache.put(requisicao, copia));
          return resposta;
        })
        .catch(() => caches.match(requisicao))
    );
    return;
  }

  evento.respondWith(
    caches.match(requisicao).then((emCache) => {
      if (emCache) return emCache;
      return fetch(requisicao).then((resposta) => {
        if (resposta && resposta.status === 200 && resposta.type === "basic") {
          const copia = resposta.clone();
          caches.open(VERSAO).then((cache) => cache.put(requisicao, copia));
        }
        return resposta;
      });
    }).catch(() => caches.match("index.html"))
  );
});
