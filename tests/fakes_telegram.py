"""Fakes minimos dos objetos do python-telegram-bot para testar handlers.

Permitem chamar os handlers async diretamente (sem rede, sem bot real),
gravando tudo que o bot "enviaria". Os asserts dos testes devem preferir
comportamento (estado no banco, destinatario, substring curta) a frases
inteiras, para nao quebrarem a cada ajuste de texto das mensagens.
"""


class FakeUser:
    """Usuario do Telegram: so o id importa para os handlers."""

    def __init__(self, user_id: int):
        self.id = user_id


class FakePhotoSize:
    """Item de ``message.photo`` (o handler usa o ultimo da lista)."""

    def __init__(self, file_id: str):
        self.file_id = file_id


class FakeDocument:
    """``message.document`` (PDF ou imagem enviada como arquivo)."""

    def __init__(self, file_id: str, mime_type: str = "", file_name: str = ""):
        self.file_id = file_id
        self.mime_type = mime_type
        self.file_name = file_name


class FakeFile:
    """Retorno de ``bot.get_file``; download e um no-op (o OCR e mockado)."""

    def __init__(self, file_size: int = 1024):
        self.file_size = file_size

    async def download_to_drive(self, path):
        pass


class FakeMessage:
    """Mensagem recebida; ``reply_text`` acumula as respostas em ``replies``."""

    def __init__(self, text=None, photo=(), document=None,
                 reply_to_message=None, caption=None):
        self.text = text
        self.photo = list(photo)
        self.document = document
        self.reply_to_message = reply_to_message
        self.caption = caption
        self.replies: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return FakeMessage(text=text)


class FakeCallbackQuery:
    """Clique em botao inline; grava ``answers`` e edicoes de legenda."""

    def __init__(self, data: str, from_user: FakeUser, message: FakeMessage | None = None):
        self.data = data
        self.from_user = from_user
        self.message = message or FakeMessage(caption="⚠️ Comprovante para revisão")
        self.answers: list[tuple[str | None, bool]] = []
        self.captions: list[str] = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_caption(self, caption=None, **kwargs):
        self.captions.append(caption)


class FakeBot:
    """Grava tudo que seria enviado, como tuplas (tipo, chat_id, conteudo)."""

    def __init__(self, file_size: int = 1024):
        self.sent: list[tuple[str, int, str | None]] = []
        self._file_size = file_size

    async def get_file(self, file_id):
        return FakeFile(self._file_size)

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(("message", chat_id, text))

    async def send_photo(self, chat_id, photo, caption=None, **kwargs):
        self.sent.append(("photo", chat_id, caption))

    async def send_document(self, chat_id, document, caption=None, **kwargs):
        self.sent.append(("document", chat_id, caption))

    def enviados_para(self, chat_id):
        """Conteudos (texto/caption) enviados a um chat especifico."""
        return [c for _, cid, c in self.sent if cid == chat_id]


class FakeContext:
    """Substitui ``ContextTypes.DEFAULT_TYPE``: bot + user_data + args."""

    def __init__(self, bot: FakeBot | None = None, args=None):
        self.bot = bot or FakeBot()
        self.user_data: dict = {}
        self.args = args or []


class FakeUpdate:
    """Update com usuario + mensagem OU callback_query."""

    def __init__(self, user_id: int, message: FakeMessage | None = None,
                 callback_query: FakeCallbackQuery | None = None):
        self.effective_user = FakeUser(user_id)
        self.message = message
        self.callback_query = callback_query


def update_texto(user_id: int, texto: str) -> FakeUpdate:
    """Atalho: update de mensagem de texto simples."""
    return FakeUpdate(user_id, message=FakeMessage(text=texto))


def update_foto(user_id: int, file_id: str = "foto-1") -> FakeUpdate:
    """Atalho: update com uma foto (comprovante)."""
    return FakeUpdate(user_id, message=FakeMessage(photo=[FakePhotoSize(file_id)]))


def update_pdf(user_id: int, file_id: str = "pdf-1") -> FakeUpdate:
    """Atalho: update com documento PDF (comprovante)."""
    doc = FakeDocument(file_id, mime_type="application/pdf", file_name="comprovante.pdf")
    return FakeUpdate(user_id, message=FakeMessage(document=doc))
