import os
import re
import json
import uuid
import requests
from typing import List, Optional
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup

# -----------------------------------------------------------------------------
# Настройки приложения Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)

# Чтобы в выдаваемом JSON русские символы не экранировались \uXXXX
app.config['JSON_AS_ASCII'] = False

# -----------------------------------------------------------------------------
# Захардкоженные настройки для GigaCha
# -----------------------------------------------------------------------------
CLIENT_ID = ''
SECRET = ''
AUTH = ''

# -----------------------------------------------------------------------------
# Ключ для Bing Search API
# -----------------------------------------------------------------------------
BING_SEARCH_KEY = ""
BING_SEARCH_ENDPOINT = ""

# -----------------------------------------------------------------------------
# Функция получения токена GigaChat
# -----------------------------------------------------------------------------
def get_gigachat_token(auth_token: str, scope: str = "GIGACHAT_API_PERS") -> str:
    rq_uid = str(uuid.uuid4())
    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
        'RqUID': rq_uid,
        'Authorization': f"Basic {auth_token}"
    }
    payload = {'scope': scope}
    
    response = requests.post(url, headers=headers, data=payload, verify=False)
    response.raise_for_status()
    return response.json()['access_token']

# -----------------------------------------------------------------------------
# Функция отправки запроса к GigaChat
# -----------------------------------------------------------------------------
def gigachat_completion_message(access_token: str, user_message: str) -> str:
    url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
    payload = json.dumps({
        "model": "GigaChat",
        "messages": [
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7,
        "top_p": 0.9,
        "n": 1,
        "stream": False,
        "max_tokens": 512,
        "repetition_penalty": 1,
        "update_interval": 0
    })
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }

    response = requests.post(url, headers=headers, data=payload, verify=False)
    response.raise_for_status()
    data = response.json()
    if "choices" in data and len(data["choices"]) > 0:
        return data["choices"][0]["message"]["content"]
    else:
        return "Извините, не удалось получить ответ от GigaChat."

# -----------------------------------------------------------------------------
# Поиск в Bing
# -----------------------------------------------------------------------------
def search_links_bing(query: str, count: int = 3) -> List[str]:
    if not BING_SEARCH_KEY:
        return []
    
    headers = {"Ocp-Apim-Subscription-Key": BING_SEARCH_KEY}
    params = {"q": query, "count": count}
    
    try:
        resp = requests.get(BING_SEARCH_ENDPOINT, headers=headers, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        web_pages = data.get("webPages", {})
        value = web_pages.get("value", [])
        links = []
        for item in value:
            links.append(item["url"])
            if len(links) >= count:
                break
        return links
    except Exception as e:
        print(f"Error in Bing search: {e}")
        return []

# -----------------------------------------------------------------------------
# Получение текста с первой ссылки
# -----------------------------------------------------------------------------
def extract_text_from_url(url: str) -> str:
    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        text = " ".join([p.get_text() for p in paragraphs if p.get_text()])
        return text
    except Exception as e:
        print(f"Error while extracting text from {url}: {e}")
        return ""

# -----------------------------------------------------------------------------
# Вспомогательная функция, проверяем, содержит ли вопрос варианты (1..10)
# -----------------------------------------------------------------------------
def check_if_multiple_choice(query: str) -> bool:
    for i in range(1, 11):
        if f"\n{i}" in query:
            return True
    return False

# -----------------------------------------------------------------------------
# Функция для поиска правильного ответа в ответе модели
# -----------------------------------------------------------------------------
def find_answer_in_choices(model_answer: str, choices: List[str]) -> Optional[int]:
    # Извлекаем все числа из ответа модели (например, 2009)
    numbers_in_answer = re.findall(r'\d+', model_answer)
    
    for number in numbers_in_answer:
        for choice in choices:
            # Извлекаем числа из варианта ответа (например, 2007, 2009 и т.д.)
            choice_number = choice.split(".")[0].strip()
            if number == choice_number:
                # Если найдено совпадение с номером варианта, возвращаем его
                return int(choice_number)
    
    return None

# -----------------------------------------------------------------------------
# Формируем основной эндпоинт
# -----------------------------------------------------------------------------
@app.route("/api/request", methods=["POST"])
def handle_request():
    req_data = request.get_json(force=True)
    user_query = req_data.get("query", "")
    question_id = req_data.get("id", 0)

    # Варианты ответа
    choices = re.findall(r"\d+\.", user_query)

    # Попробуем извлечь номер правильного ответа из запроса
    answer_choice = None

    # Поиск источников (новости или Bing)
    sources_links = []
    if "новост" in user_query.lower():
        sources_links.append("https://news.itmo.ru/")  
    elif "меропр" in user_query.lower() or "событ" in user_query.lower():
        sources_links.append("https://itmo.events/?ysclid=m6l2itn6e8862243454")
    elif "год" in user_query.lower() or "лет" in user_query.lower() or "истор" in user_query.lower() or "сущ" in user_query.lower():
        sources_links.append("https://itmo.ru/ru/page/211/istoriya_universiteta_itmo.htm?ysclid=m6l3tmnxza657002175")
    elif "направ" in user_query.lower() or "адрес" in user_query.lower() or "факультет" in user_query.lower():
        sources_links.append("https://itmo.ru/ru/")
    else:
        search_results = search_links_bing(user_query, 3)
        sources_links.extend(search_results)

    if len(sources_links) == 0 and "итмо" in user_query.lower():
        sources_links.append("https://itmo.ru/ru/")

    # Оставим не более 3 ссылок
    sources_links = sources_links[:3]

    # Если есть ссылки, извлекаем текст с них и передаем это в GigaChat
    page_texts = ""
    for link in sources_links:
        page_texts += extract_text_from_url(link) + "\n"

    # Формируем prompt для GigaChat
    prompt = f"Ответь на вопрос об Университете ИТМО: {user_query}\n"
    if page_texts:
        prompt += f"Вот информация для ответа, опирайся на неё: {page_texts}\n"
    if sources_links:
        prompt += "Источники для ответа:\n" + "\n".join(sources_links)
    
    # Отправка запроса к GigaChat
    gigachat_token = get_gigachat_token(AUTH)
    model_answer = gigachat_completion_message(gigachat_token, prompt)

    # Извлекаем правильный ответ из модели, сопоставляем с вариантами
    answer_choice_from_model = find_answer_in_choices(model_answer, choices)

    if answer_choice_from_model is not None:
        answer_choice = answer_choice_from_model

    # Если вариантов нет, то answer должно быть None
    if not check_if_multiple_choice(user_query):
        answer_choice = None

    # Формируем итоговый JSON
    final_json = {
        "id": question_id,
        "answer": answer_choice if answer_choice is not None else None,
        "reasoning": f"Ответ сгенерирован моделью GigaChat:\n{model_answer}",
        "sources": sources_links if sources_links else []
    }

    return jsonify(final_json)

# -----------------------------------------------------------------------------
# Точка входа: запуск Flask-приложения
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
