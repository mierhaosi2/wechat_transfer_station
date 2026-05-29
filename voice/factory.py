"""
voice factory
"""


def create_voice(voice_type):
    """
    create a voice instance
    :param voice_type: voice type code
    :return: voice instance
    """
    raise RuntimeError(f"Unsupported voice type: {voice_type}")
