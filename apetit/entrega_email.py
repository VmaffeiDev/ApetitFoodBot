"""Entregar o codigo na caixa de entrada. So isso, e por SMTP.

Separado de `apetit/identidade.py` de proposito: la e a regra de quem entra, que
precisa rodar em teste sem rede nenhuma. Aqui e o unico pedaco que fala com o
mundo, e por isso o unico que pode falhar por motivo que nao e do Apetit — DNS,
senha trocada, provedor fora do ar.

**Nao existe modo "finge que enviou".** Um remetente que engolisse a falha em
silencio produziria o pior resultado possivel: a pessoa esperando um codigo que
nunca sai, e o servidor achando que entregou. Sem configuracao, `montar` devolve
`None` e a rota de entrar responde que a entrada por codigo nao esta disponivel.

**O codigo nao vai para log.** Nem em erro. O assunto e o corpo sao montados e
usados; o que sobra para o log e o endereco e se deu certo.
"""

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)

ASSUNTO = "Seu codigo de acesso ao Apetit"

CORPO = """Ola,

Seu codigo de acesso ao Apetit e: {codigo}

Ele vale por {minutos} minutos e serve uma vez so.

Se nao foi voce que pediu, pode ignorar este e-mail — sem o codigo, ninguem
entra na sua conta. Nao responda com o codigo para ninguem: a equipe da Apetit
nunca vai pedir isso.
"""


class Remetente:
    """Manda o codigo por SMTP. Uma conexao por envio.

    Conexao por envio, e nao conexao guardada: o piloto tem quinze pessoas, o
    volume e de alguns e-mails por dia, e uma conexao dormindo por horas quebra
    sozinha e falha justamente no proximo envio.
    """

    def __init__(self, servidor: str, porta: int, usuario: str, senha: str,
                 remetente: str, minutos: int = 10, tls: bool = True):
        self.servidor = servidor
        self.porta = porta
        self.usuario = usuario
        self.senha = senha
        self.remetente = remetente
        self.minutos = minutos
        self.tls = tls

    def __call__(self, email: str, codigo: str) -> None:
        mensagem = EmailMessage()
        mensagem["Subject"] = ASSUNTO
        mensagem["From"] = self.remetente
        mensagem["To"] = email
        mensagem.set_content(CORPO.format(codigo=codigo, minutos=self.minutos))

        with smtplib.SMTP(self.servidor, self.porta, timeout=20) as smtp:
            if self.tls:
                smtp.starttls()
            if self.usuario:
                smtp.login(self.usuario, self.senha)
            smtp.send_message(mensagem)
        # Endereco sim, codigo nunca.
        logger.info("codigo de entrada enviado para %s", email)


def montar(minutos: int = 10) -> Remetente | None:
    """O remetente do ambiente, ou `None` quando nao ha como enviar.

    `None` e nao uma excecao: subir o servidor precisa funcionar sem SMTP
    configurado — o cardapio e o app continuam de pe, so a entrada por codigo
    e que fica indisponivel, e a rota diz isso.
    """
    servidor = os.getenv("APETIT_SMTP_HOST", "").strip()
    remetente = os.getenv("APETIT_SMTP_FROM", "").strip()
    if not servidor or not remetente:
        return None
    return Remetente(
        servidor=servidor,
        porta=int(os.getenv("APETIT_SMTP_PORT", "587")),
        usuario=os.getenv("APETIT_SMTP_USER", "").strip(),
        senha=os.getenv("APETIT_SMTP_PASSWORD", ""),
        remetente=remetente,
        minutos=minutos,
        tls=os.getenv("APETIT_SMTP_TLS", "1").strip() not in ("0", "false", "no"),
    )
