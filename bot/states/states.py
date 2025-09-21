from aiogram.fsm.state import State, StatesGroup


class States(StatesGroup):
    waiting_for_prompt = State()


# ====== ПЛАНИРОВКИ (визуализация плана/чертежа) ======
class FloorPlanStates(StatesGroup):
    waiting_for_file = State()                 # ждём план/чертёж (image/pdf/ссылка)
    waiting_for_visualization_style = State()  # выбор: sketch | realistic
    waiting_for_style = State()                # выбор интерьерного стиля


# ====== ДИЗАЙН (редизайн по фото) ======
class RedesignStates(StatesGroup):
    waiting_for_file = State()        # было waiting_for_photo — унифицируем с ZeroDesign
    waiting_for_room_type = State()   # выбор типа помещения
    waiting_for_style = State()       # выбор интерьерного стиля


# ====== ДИЗАЙН С НУЛЯ (zero-design) ======
class ZeroDesignStates(StatesGroup):
    waiting_for_file = State()
    waiting_for_room_type = State()
    waiting_for_furniture = State()
    waiting_for_style = State()


# ====== ПРОЧЕЕ (как было) ======
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
    waiting_city_mode = State()
    waiting_city_input = State()
    waiting_address = State()
    waiting_deal_type = State()
    waiting_deal_custom = State()
    waiting_situation = State()
    waiting_style = State()
    showing_summary = State()
    browsing_variants = State()
    history_search = State()
    waiting_tone = State()
    waiting_length = State()


class SummaryStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_audio = State()
    ready_to_generate = State()


# ====== ADMIN ======
class PriceStates(StatesGroup):
    waiting_for_new_price = State()


class ChangeStartText(StatesGroup):
    GetText = State()


class ChangeTextOfRates(StatesGroup):
    GetText = State()


class CreateNewPostState(StatesGroup):
    GetText = State()


class CreateMailing(StatesGroup):
    GetText = State()


class EditPostState(StatesGroup):
    waiting_for_new_text = State()
    message_id = State()


class CalendarStates(StatesGroup):
    Selecting = State()  # показан календарь, ждём cal.date:YYYY-MM-DD
