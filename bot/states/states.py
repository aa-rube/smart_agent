# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\states\states.py

from aiogram.fsm.state import State, StatesGroup


class States(StatesGroup):
    waiting_for_prompt = State()


class DesignStates(StatesGroup):
    waiting_for_file = State()
    # waiting_for_plan_type = State()
    waiting_for_visualization_style = State()
    waiting_for_style = State()


class RedesignStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_room_type = State()
    waiting_for_style = State()


class ZeroDesignStates(StatesGroup):
    waiting_for_file = State()
    waiting_for_room_type = State()
    waiting_for_furniture = State()
    waiting_for_style = State()


class ObjectionStates(StatesGroup):
    waiting_for_question = State()


class DescriptionStates(StatesGroup):
    waiting_for_type = State()
    waiting_for_class = State()
    waiting_for_complex = State()
    waiting_for_area = State()
    waiting_for_comment = State()

class FeedbackStates(StatesGroup):
    waiting_client = State()
    waiting_agent = State()
    waiting_company = State()
    waiting_city_mode = State()  # выбор способа ввода
    waiting_city_input = State()
    waiting_address = State()
    waiting_deal_type = State()
    waiting_deal_custom = State()
    waiting_situation = State()
    waiting_style = State()
    showing_summary = State()
    browsing_variants = State()
    history_search = State()