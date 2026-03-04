# 🎵 EIMOR MUSIC — Деплой на хостинг

## Файли для завантаження на сервер:
```
app.py          ← Flask сервер
index.html      ← Фронтенд
requirements.txt
```

## Швидкий старт (VPS / Linux):

```bash
# 1. Встановити залежності
pip install -r requirements.txt

# 2. Запуск (розробка)
python app.py

# 3. Запуск (продакшн з gunicorn)
pip install gunicorn
FLASK_ENV=production gunicorn -w 2 -b 0.0.0.0:5000 app:app
```

## Змінні оточення (опціонально):
```
ADMIN_EMAIL=your@email.com
ADMIN_PASSWORD=yourpassword
SECRET_KEY=random_secret_string
FLASK_ENV=production
```

## Для Railway / Render / Heroku:
Додай `Procfile`:
```
web: gunicorn app:app
```
