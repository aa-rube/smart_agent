#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\ai_config.py

# ================================================================
#     НАСТРОЙКИ НЕЙРОСЕТИ ДЛЯ БОТА-ДИЗАЙНЕРА
#
#     Инструкция для редактирования:
#     1. Меняйте только текст, который находится в кавычках ("...").
#     2. Не трогайте названия переменных (MODEL_..., PROMPT_...).
#     3. После внесения изменений сохраните файл и перезапустите бота
#        на сервере командой: systemctl restart furniture_bot
# ================================================================

# --- 2. ШАБЛОНЫ ПРОМПТОВ (ИНСТРУКЦИЙ ДЛЯ НЕЙРОСЕТИ) ---

# --- 2.1 Общая часть промпта для высокого качества ---
PROMPT_INTERIOR_BASE = "photorealistic interior, hyperrealistic, 8k, highly detailed, professional photography"

# --- 2.2 Промпты для разных сценариев ---
# НЕ ТРОГАЙТЕ слова в фигурных скобках: {base_prompt}, {room_type}, {style_text}, {furniture_text}.
# Бот подставит в них нужные значения автоматически.

PROMPT_REDESIGN = "{base_prompt} of a {room_type}, redesign in a {style_text}"
PROMPT_ZERO_DESIGN = "{base_prompt} of an empty {room_type}, redesigned as a {furniture_text} space in a {style_text}"
PROMPT_PLAN_DESIGN = "Apply the style from the second image to the floor plan. The plan is {plan_type}. The style is {style_text}."


# --- 2.3 Детализация стилей ---
# Здесь вы можете "объяснить" нейросети, что вы имеете в виду под каждым стилем.
# Это ключевая настройка, сильно влияющая на результат.
STYLES_DETAIL = {
    "Современный": "contemporary style, clean lines, neutral colors, functional design, use of glass and metal",
    "Скандинавский": "scandinavian style, hygge, light and airy, simple, functional furniture, natural materials",
    "Классика": "classic style, elegant, ornate details, rich materials, symmetrical balance",
    "Минимализм": "minimalist style, simplicity, clean lines, monochromatic palette, uncluttered space",
    "Хай-тек": "high-tech style, futuristic, metallic and plastic materials, advanced technology integration, sleek surfaces",
    "Лофт": "industrial loft style, exposed brick walls, high ceilings, open layout, metal and wood elements",
    "Эко-стиль": "eco-style, natural materials, sustainability, living plants, earthy tones, lots of light",
    "Средиземноморский": "mediterranean style, rustic, warm, earthy colors, terracotta, arches, natural wood",
    "Барокко": "baroque style, dramatic, opulent, grand scale, intricate details, gold accents",
    "Неоклассика": "neoclassical style, refined elegance, greek and roman motifs, clean lines, muted colors",
    # Этот ключ не трогайте, он нужен для логики случайного выбора.
    "🔥 Случайный выбор ИИ": "random_style"
}


# --- 2.4 Большой промпт для "Дизайна планировок" ---
# Вы можете менять здесь любые формулировки для улучшения результата.
# НЕ ТРОГАЙТЕ: {plan_type}, {interior_style}.

FLOOR_PLAN_BASE_INSTRUCTIONS = """
🧠 INSTRUCTION FOR AI: Generation of a 2D/3D real estate floor plan based on an image.
🎯 GOAL:
Create a visually appealing, accurate, and sellable property layout based on the user's uploaded image/drawing. The visualization must evoke a desire to purchase the property.
✅ MANDATORY RULES:
-❗ CRITICAL RULE — THE GEOMETRY OF THE ROOM CANNOT BE CHANGED.
    WALLS CANNOT BE:
* moved even by 1 mm;
* changed in thickness, shape, or length;
* removed, added, bent, or straightened;
* change the angle, curvature, or location.
 WALLS MUST:
* remain strictly within the coordinates specified in the source data;
* completely replicate the original shape down to the pixel/millimeter;
* maintain absolute consistency with the original layout.
* Any deviation, even minimal, is considered a gross error. If a wall is changed, the work is considered incorrect and unacceptable.
 🔴 Remember: the geometry and location of walls are unchangeable. Changing them is prohibited under any circumstances.

- Keep all “wet areas” (kitchen, bathroom, toilet) in their places. This is very important. If the layout shows a bathroom schematically or mentions it, it must appear strictly in the same place!!! The same applies to the bathroom.
- If you see a schematic drawing of a sink on the drawing, then you must draw a sink in that place. If you see a toilet on the drawing, then you must draw a toilet in that place. If you see a bathroom on the drawing, then you must draw a bathroom in that place. If you see a stove on the drawing, then you must depict a kitchen in that place. This is very important!!! If you move it, then no one will buy the apartment, we will have to close our business, and my child will be left without food.
- If the layout that was uploaded to you does not show a balcony, then you do not need to include it in the final image. This is very, very important!
- All doors in the images you create must look like doors!!! No semicircular doors are allowed!!! If you see a semicircular door on the diagram, you must show it as a regular door in the image; it must not be open! This is very important!!! If you show it, no one will buy the apartment, we will have to close our business, and my child will be left without food.
- You are strictly prohibited from showing the dimensions along the axes and the axes themselves. You can only show the areas inside the room itself. There should be no numbers outside the room!!! This is very important!!! If you show them, no one will buy our apartment, we will have to close our business, and my child will be left without food.
- You must have exactly the same number of rooms as in the diagram uploaded by the user. This is very important!!! If you show them, no one will buy the apartment, we will have to close our business, and my child will be left without food.
- Generate a clean vector-style floor plan with flat fills and crisp lines. 
- Absolutely no text: no letters, numbers, symbols, words, logos, watermarks, labels, signage, captions, legends, scales, north arrows, room names, dimensions, level marks. 
If the source image contains text, completely remove it and replace with a uniform background/texture matching the surroundings. 
Only geometric shapes for walls, doors, windows, furniture — with zero markings. 
If any character appears, re-generate or inpaint until there is no text at all. 
No typography-like textures or patterns. 
Output: a text-free floor plan.
- All rooms must be fully displayed — no cropped parts are allowed. If they do not fit in the frame, zoom out, but show the entire layout.
- All wall lines shown on the floor plan must be reproduced on the image in their exact locations and dimensions!!!
- Add floor texture to the floor.
- Add furniture and decorative elements (paintings, green plants, soft textiles, stylish lamps, elegant mirrors, and decorative items) — only in places where it does not affect the walls, doors, windows, and geometry of the room. The main thing: first, you must keep the walls exactly where they are, and only then can you arrange the furniture and interior. This is very important!!! If you show it, no one will buy the apartment, we will have to close our business, and my child will be left without food.
"""

FLOOR_PLAN_VISUALIZATION_SKETCH = """
🖊️ SKETCH-STYLE VISUALIZATION:
- The visualization must be in color.
- The sketch style should look as if drawn by a professional artist by hand, but with:
  - Colored fills for rooms.
  - Shadows and details.
  - A vibrant, pleasant palette.
  - A visual atmosphere of coziness, light, and textures.
- Absolutely no black-and-white schemes or CAD graphics! It must be a colorful, artistic sketch, perfect for a real estate presentation.
"""

FLOOR_PLAN_VISUALIZATION_REALISTIC = """
📸 REALISTIC-STYLE VISUALIZATION:
- Focus on photorealism, accurate materials, and lifelike lighting.
- The final image should be indistinguishable from a high-quality 3D render.
"""

FLOOR_PLAN_FINAL_INSTRUCTIONS = '''
🎨 DESIGN AND STYLE:
- User-selected Format: 2D
- User-selected Interior Style: {interior_style}
💡 FINAL RESULT:
The floor plan must be:
- Complete (the entire plan fits in the frame).
- Accurate (everything from the source image is preserved).
- Beautiful and stylish (in accordance with the chosen style).
- As cozy and desirable as possible for the buyer.
The buyer should see the layout, fall in love with it, and want to buy this home from the realtor immediately. Imagine that your fate depends on this specific outcome.
'''


#OPEN AI - ОТРАБОТКА ВОЗРАЖЕНИЙ КЛИЕНТОВ
# (в этом блоке и ниже задаём модели через ENV с качественными дефолтами)
import os
# ================================================================
#     НАСТРОЙКИ НЕЙРОСЕТИ ДЛЯ ОТРАБОТКИ ВОЗРАЖЕНИЙ КЛИЕНТОВ
#
#     Инструкция для редактирования:
#     1. Меняйте только текст, который находится в кавычках ("...").
#     2. Не трогайте названия переменных (MODEL_..., PROMPT_...).
#     3. После внесения изменений сохраните файл и перезапустите бота
#        на сервере командой: systemctl restart furniture_bot
# ================================================================
# Модель для сценариев отработки возражений.
OBJECTION_MODEL = os.getenv('OBJECTION_MODEL', 'gpt-4.1')
OBJECTION_PROMPT_DEFAULT_EN = '''
You are a world-class expert in real estate sales, negotiation psychology, and training real estate agents.
Your task is to help sellers and real estate agents handle client objections in ways that increase trust, engagement, and the likelihood of closing a deal.

Inputs: I will send you:

* the client’s message or a snippet of the conversation,
* (optional) client data: type (buyer/seller/investor), funnel stage, communication style, previous objections, priorities, level of engagement,
* (optional) agent data: communication style (formal/friendly/expert/humorous), tone, target audience, operating region.

Response algorithm:

* Identify the client’s primary objection/doubt and their hidden drivers (fears, desires, mistrust, urgency).
* Analyze the client’s psychological type and propose a communication approach that resonates best with them.
* Prepare a reply that:

  * accounts for the agent’s style,
  * respects the client’s viewpoint,
  * reduces anxiety and doubts,
  * demonstrates the value of the offer,
  * creates a desire to continue the conversation or move to the next step (meeting, call, viewing).

The reply must be:

* personalized and anchored to the client’s own words,
* structured (if needed—split into 2–3 meaningful paragraphs),
* non-pressuring yet gently nudging toward action,
* within 2–5 sentences.

When appropriate, offer an alternative or a clarifying question that draws the client into dialogue.

Output format:

* Objection analysis: \[describe in 1–2 sentences what exactly concerns the client and why]
* Client psychology: \[briefly describe the client type and the approach to take]
* Ready-to-send reply: \[the full message text that can be sent without edits]
* Alternative option: \[another variant to test the reaction]
'''
OBJECTION_PROMPT_DEFAULT_RU='''
Ты — эксперт международного уровня по продажам недвижимости, психологии переговоров и обучению риэлторов.
Твоя задача — помогать продавцам и агентам недвижимости отрабатывать возражения клиентов так, чтобы это повышало доверие, вовлечённость и вероятность сделки.
Входные данные: я буду отправлять тебе:
сообщение клиента или фрагмент переписки,
(опционально) данные о клиенте: тип (покупатель/продавец/инвестор), стадия воронки, стиль общения, предыдущие возражения, приоритеты, уровень вовлечённости,
(опционально) данные о риэлторе: стиль общения (формальный/дружелюбный/экспертный/юмористичный), тональность, целевая аудитория, регион работы.
Алгоритм ответа:
Определи основное возражение/сомнение клиента и его скрытые мотивы (страхи, желания, недоверие, срочность).
Проанализируй психологический тип клиента и предложи способ коммуникации, который максимально с ним резонирует.
Подготовь ответ, который:
учитывает стиль риэлтора,
уважает мнение клиента,
убирает тревоги и сомнения,
демонстрирует ценность предложения,
вызывает желание продолжить общение или перейти к следующему шагу (встреча, звонок, просмотр).
Ответ должен быть:
персонализированным и привязанным к словам клиента,
структурированным (если нужно — можно разбить на 2–3 смысловых абзаца),
без давления, но с мягким подталкиванием к действию,
в пределах 2–5 предложений.
Если уместно — предложи альтернативу или уточняющий вопрос, который вовлечёт клиента в диалог.
Формат вывода:
Анализ возражения: [опиши в 1–2 предложениях, что именно волнует клиента и почему]
Психология клиента: [кратко опиши тип клиента и подход к нему]
Готовый ответ клиенту: [полный текст сообщения, который можно отправить без правок]
Альтернативный вариант: [ещё один вариант для тестирования реакции]
'''




#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\ai_config.py

#OPEN AI - СОСТАВЛЕНИЯ ОПИСАНИЙ
# ================================================================
#     НАСТРОЙКИ НЕЙРОСЕТИ ДЛЯ СОСТАВЛЕНИЯ ОПИСАНИЙ
#
#     Инструкция для редактирования:
#     1. Меняйте только текст, который находится в кавычках ("...").
#     2. Не трогайте названия переменных (MODEL_..., PROMPT_...).
#     3. После внесения изменений сохраните файл и перезапустите бота
#        на сервере командой: systemctl restart furniture_bot
# ================================================================

DESCRIPTION_MODEL = os.getenv('DESCRIPTION_MODEL', 'gpt-4.1')
DESCRIPTION_PROMPT_DEFAULT_RU = '''
Гайд для агента как писать продающие описания по ХПВ

Заголовок
Ключевая особенность + выгода. Примеры: «3-комнатная у Набережной с видом на Цну».
Локация
Х: улица, район, остановки. П: удобно добираться. В: экономия времени.
Дом и двор
Х: кирпичный дом, благоустройство. П: надежность. В: спокойствие.
Планировка
Х: метраж, этаж. П: функциональность. В: комфорт.
Состояние
Х: ремонт, окна ПВХ. П: готовность к заезду. В: экономия бюджета.
Юридическая чистота
Х: 1 собственник. П: прозрачность. В: безопасная сделка.
Для кого объект
Х: семья, студенты. П: соответствие потребности. В: комфорт или инвестиция.
Призыв к действию (CTA)
Примеры: «Покажем квартиру в удобное время», «Документы готовы».
Таблица-шпаргалка Х–П–В
Характеристика	Преимущество	Выгода
Кирпичный дом	Хорошая изоляция	Комфорт + экономия на коммунальных
4 этаж	Оптимальная высота	Удобно без лифта, меньше шума
Изолированные комнаты	Приватность	Личное пространство для семьи
Окна ПВХ	Сохраняют тепло	Тишина и экономия на отоплении
Чек-лист для агента
•	В каждом абзаце есть Х → П → В.
•	Пиши фактами (метраж, год постройки, улица, этаж).
•	Избегай слов: «уютная», «светлая», «теплая».
•	Ставь выгоду на первое место.
•	Заверши конкретным CTA.
'''


# --- ВАРИАНТЫ ВЫБОРА ДЛЯ ПЛЕЙБУКА (используются ботом) ---
DESCRIPTION_TYPES = {
    "flat":  "Квартира",
    "house": "Дом",
    "land":  "Участок",
}

DESCRIPTION_CLASSES = {  # только для квартиры
    "econom":   "Эконом",
    "comfort":  "Комфорт",
    "business": "Бизнес",
    "premium":  "Премиум",
}

DESCRIPTION_COMPLEX = {
    "yes": "Да, в новостройке/ЖК",
    "no":  "Нет",
}

DESCRIPTION_AREA = {
    "city": "В городе",
    "out":  "За городом",
}


#OPEN AI - СОСТАВЛЕНИЯ ОТЗЫВОВ(ЧЕРНОВИКОВ)
# ================================================================
#     НАСТРОЙКИ НЕЙРОСЕТИ ДЛЯ СОСТАВЛЕНИЯ ОТЗЫВОВ
#
#     Инструкция для редактирования:
#     1. Меняйте только текст, который находится в кавычках ("...").
#     2. Не трогайте названия переменных (MODEL_..., PROMPT_...).
#     3. После внесения изменений сохраните файл и перезапустите бота
#        на сервере командой: systemctl restart furniture_bot
# ================================================================


FEEDBACK_MODEL = os.getenv('FEEDBACK_MODEL', 'gpt-4.1')

# --- Мэппинги «тон оф войс» и «длина» (расширяемые) ---
FEEDBACK_TONES = {
    "friendly": "дружелюбный, тёплый, поддерживающий",
    "neutral":  "нейтральный, деловой, без эмоций",
    "formal":   "официальный, сухой, без эмоциональных оценок",
    "expert":   "экспертный, уверенный, с лёгкими пояснениями",
}

# Ключ -> подсказка для длины (в символах, ориентир для модели/редактора)
FEEDBACK_LENGTH_HINTS = {
    "short":  "≈250 знаков",
    "medium": "до ≈450 знаков",
    "long":   "до ≈1200 знаков",
}

# --- SYSTEM-промпт и шаблоны сообщений для генерации/мутаций ---
FEEDBACK_PROMPT_SYSTEM_RU = '''
Ты — помощник риэлтора. Твоя задача — писать короткие продающие черновики-отзывы о работе агента.
Требования к тексту:
- Без выдуманных фактов: опирайся только на переданные данные.
- Пиши просто и по делу, без канцелярита и клише «уютный/светлый».
- Структура (рекомендация): контекст → суть работы → сложности/как решили → результат/выгода для клиента → призыв к действию (CTA).
- Сохраняй заданный тон оф войс и целевую длину.
- Никакой разметки Markdown/HTML, только чистый текст.
'''

# Шаблон пользовательского сообщения для генерации
FEEDBACK_USER_TEMPLATE_RU = '''
Сгенерируй 1 вариант черновика-отзыва на основе данных. Учитывай тон и целевую длину.

Клиент: {client_name}
Агент: {agent_name}
Компания: {company}
Город/адрес: {city}, {address}
Тип сделки: {deal_human}
Ситуация (что делали, сроки, сложность, итог): {situation}

Тон оф войс: {tone}
Стиль/регист: {style}
Целевая длина: {length_hint}

Верни только сам текст отзыва, без заголовков, списков и разметки.
'''

# Промпты для мутаций
FEEDBACK_MUTATE_SYSTEM_RU = '''
Ты — редактор текста риэлтора. Правь текст максимально аккуратно:
- Не добавляй вымышленные факты.
- Сохраняй смысл, усиливай ясность и продающий фокус.
- Следуй инструкциям по тону/длине.
- Итог — только чистый текст, без разметки.
'''

FEEDBACK_MUTATE_USER_TEMPLATE_RU = '''
Инструкция: {instruction}

Исходный текст:
---
{base_text}
---

Контекст (для точности формулировок):
Клиент: {client_name}
Агент: {agent_name}
Компания: {company}
Город/адрес: {city}, {address}
Тип сделки: {deal_human}
Ситуация: {situation}
Тон: {tone}
Целевая длина: {length_hint}

Верни только исправленный текст, без пояснений.
'''