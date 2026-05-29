from bot.bot_factory import create_bot
from bridge.context import Context
from bridge.reply import Reply
from common import const
from common.log import logger
from common.singleton import singleton
from config import conf
from voice.factory import create_voice


@singleton
class Bridge(object):
    def __init__(self):
        model = conf().get("model", const.DIFY)
        if model == const.WEBHOOK:
            chat_type = const.WEBHOOK
        else:
            chat_type = const.DIFY
        self.btype = {
            "chat": chat_type,
            "voice_to_text": conf().get("voice_to_text", ""),
            "text_to_voice": conf().get("text_to_voice", ""),
        }
        self.bots = {}
        self.chat_bots = {}

    def get_bot(self, typename):
        if self.bots.get(typename) is None:
            logger.info("create bot {} for {}".format(self.btype[typename], typename))
            if typename in ("text_to_voice", "voice_to_text"):
                self.bots[typename] = create_voice(self.btype[typename])
            elif typename == "chat":
                self.bots[typename] = create_bot(self.btype[typename])
        return self.bots[typename]

    def get_bot_type(self, typename):
        return self.btype[typename]

    def fetch_reply_content(self, query, context: Context) -> Reply:
        return self.get_bot("chat").reply(query, context)

    def fetch_voice_to_text(self, voiceFile) -> Reply:
        return self.get_bot("voice_to_text").voiceToText(voiceFile)

    def fetch_text_to_voice(self, text) -> Reply:
        return self.get_bot("text_to_voice").textToVoice(text)

    def find_chat_bot(self, bot_type: str):
        if self.chat_bots.get(bot_type) is None:
            self.chat_bots[bot_type] = create_bot(bot_type)
        return self.chat_bots.get(bot_type)

    def reset_bot(self):
        self.__init__()
