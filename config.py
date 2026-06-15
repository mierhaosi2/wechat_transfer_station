# encoding:utf-8

import json
import logging
import os
import pickle

from common.log import logger

available_setting = {
    # dify配置
    "dify_api_base": "https://api.dify.ai/v1",
    "dify_api_key": "app-xxx",
    "dify_app_type": "chatbot",  # chatbot / agent / workflow
    "dify_convsersation_max_messages": 5,

    # Bot触发配置
    "model": "dify",
    "single_chat_prefix": [""],
    "single_chat_reply_prefix": "",
    "single_chat_reply_suffix": "",
    "group_chat_prefix": ["@bot"],
    "group_chat_reply_prefix": "",
    "group_chat_reply_suffix": "",
    "group_chat_keyword": [],
    "group_at_off": False,
    "group_name_white_list": ["ALL_GROUP"],
    "group_name_keyword_white_list": [],
    "group_chat_in_one_session": [],
    "nick_name_black_list": [],
    "user_id_black_list": [],
    "group_welcome_msg": "",
    "trigger_by_self": False,
    "concurrency_in_session": 1,
    "group_chat_exit_group": False,
    "expires_in_seconds": 3600,
    "character_desc": "",
    "clear_memory_commands": ["#清除记忆"],

    # 语音设置
    "speech_recognition": False,
    "group_speech_recognition": False,
    "voice_reply_voice": False,
    "always_reply_voice": False,
    "voice_to_text": "",
    "text_to_voice": "",

    # 图像设置
    "image_recognition": False,
    "image_proxy": False,
    "text_to_image": "",
    "image_create_prefix": ["画", "看", "找"],
    "image_create_size": "256x256",
    "max_media_send_count": 3,
    "media_send_interval": 1,

    # 服务时间限制
    "chat_time_module": False,
    "chat_start_time": "00:00",
    "chat_stop_time": "24:00",

    # channel配置
    "channel_type": "wework",  # 支持: wx, wxy, wechatmp, wechatmp_service, wechatcom_app, wechatcom_service, wework
    "subscribe_msg": "",
    "debug": False,
    "appdata_dir": "",

    # 个人微信(wx/itchat)配置
    "hot_reload": False,

    # wechaty配置
    "wechaty_puppet_service_token": "",

    # 企业微信个人号(wework)配置
    "wework_smart": True,
    "wework_ignore_internal_users": False,        # 是否完全忽略内部用户消息（True=静音，False=仅静默计时）
    "wework_internal_user_silence_seconds": 180,  # 内部用户发言后机器人静默秒数，默认3分钟
    # 是否忽略企业微信内部用户的消息（通过 appinfo 字段判断：纯数字=个人微信，含字母=企微内部）
    "wework_ignore_internal_users": True,

    # 微信公众号配置
    "wechatmp_token": "",
    "wechatmp_port": 8080,
    "wechatmp_app_id": "",
    "wechatmp_app_secret": "",
    "wechatmp_aes_key": "",

    # 企业微信应用号(wechatcom_app / wechatcom_service)配置
    "wechatcom_corp_id": "",
    "wechatcomapp_token": "",
    "wechatcomapp_port": 9898,
    "wechatcomapp_secret": "",
    "wechatcomapp_agent_id": "",
    "wechatcomapp_aes_key": "",

    # 插件配置
    "plugin_trigger_prefix": "$",
    "use_global_plugin_config": False,

    # 消息回调 webhook（收到消息时主动调用外部接口）
    "msg_webhook_url": "",       # 外部接口地址，为空则不启用
    "msg_webhook_timeout": 5,    # 请求超时秒数
}


class Config(dict):
    def __init__(self, d=None):
        super().__init__()
        if d is None:
            d = {}
        for k, v in d.items():
            self[k] = v
        self.user_datas = {}

    def __getitem__(self, key):
        if key not in available_setting:
            raise Exception("key {} not in available_setting".format(key))
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        if key not in available_setting:
            raise Exception("key {} not in available_setting".format(key))
        return super().__setitem__(key, value)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default
        except Exception as e:
            raise e

    def get_user_data(self, user) -> dict:
        if self.user_datas.get(user) is None:
            self.user_datas[user] = {}
        return self.user_datas[user]

    def load_user_datas(self):
        try:
            with open(os.path.join(get_appdata_dir(), "user_datas.pkl"), "rb") as f:
                self.user_datas = pickle.load(f)
                logger.info("[Config] User datas loaded.")
        except FileNotFoundError:
            logger.info("[Config] User datas file not found, ignore.")
        except Exception as e:
            logger.info("[Config] User datas error: {}".format(e))
            self.user_datas = {}

    def save_user_datas(self):
        try:
            with open(os.path.join(get_appdata_dir(), "user_datas.pkl"), "wb") as f:
                pickle.dump(self.user_datas, f)
                logger.info("[Config] User datas saved.")
        except Exception as e:
            logger.info("[Config] User datas error: {}".format(e))


config = Config()


def load_config():
    global config
    config_path = "./config.json"
    if not os.path.exists(config_path):
        logger.info("配置文件不存在，将使用config-template.json模板")
        config_path = "./config-template.json"

    config_str = read_file(config_path)
    logger.debug("[INIT] config str: {}".format(config_str))
    config = Config(json.loads(config_str))

    for name, value in os.environ.items():
        name = name.lower()
        if name in available_setting:
            logger.info("[INIT] override config by environ args: {}={}".format(name, value))
            try:
                config[name] = eval(value)
            except:
                if value == "false":
                    config[name] = False
                elif value == "true":
                    config[name] = True
                else:
                    config[name] = value

    if config.get("debug", False):
        logger.setLevel(logging.DEBUG)
        logger.debug("[INIT] set log level to DEBUG")

    logger.info("[INIT] load config: {}".format(config))
    config.load_user_datas()


def get_root():
    return os.path.dirname(os.path.abspath(__file__))


def read_file(path):
    with open(path, mode="r", encoding="utf-8") as f:
        return f.read()


def conf():
    return config


def get_appdata_dir():
    data_path = os.path.join(get_root(), conf().get("appdata_dir", ""))
    if not os.path.exists(data_path):
        logger.info("[INIT] data path not exists, create it: {}".format(data_path))
        os.makedirs(data_path)
    return data_path


def subscribe_msg():
    trigger_prefix = conf().get("single_chat_prefix", [""])[0]
    msg = conf().get("subscribe_msg", "")
    return msg.format(trigger_prefix=trigger_prefix)


plugin_config = {}


def write_plugin_config(pconf: dict):
    global plugin_config
    for k in pconf:
        plugin_config[k.lower()] = pconf[k]


def pconf(plugin_name: str) -> dict:
    return plugin_config.get(plugin_name.lower())


global_config = {
    "admin_users": []
}
