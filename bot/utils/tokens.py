#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\utils\tokens.py

import bot.utils.database as db


def get_tokens(user_id):
    tokens = int(db.get_variable(user_id, 'tokens'))
    return tokens


def add_tokens(user_id, n):
    tokens = int(db.get_variable(user_id, 'tokens')) + n
    db.set_variable(user_id, 'tokens', tokens)
    return tokens


def remove_tokens(user_id, n=1):
    tokens = int(db.get_variable(user_id, 'tokens')) - n
    db.set_variable(user_id, 'tokens', tokens)
    return tokens