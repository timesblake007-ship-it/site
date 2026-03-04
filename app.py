from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import hashlib
import secrets
import re

app = Flask(__name__)

# Конфигурация для продакшена
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = 10000 * 1024 * 1024  # 100MB max
app.config['UPLOAD_FOLDER'] = 'uploads'

# CORS - в продакшене укажите свой домен
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '*').split(',')
CORS(app, origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ['*'] else '*')

# Разрешенные форматы
ALLOWED_AUDIO = {'mp3', 'wav', 'ogg', 'm4a', 'flac', 'webm', 'opus', 'aac'}
ALLOWED_IMAGES = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def validate_email(email):
    """Валидация email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_username(username):
    """Валидация username (буквы, цифры, подчеркивания, 3-20 символов)"""
    if not username:
        return True  # username опционален
    pattern = r'^[a-zA-Z0-9_]{3,20}$'
    return re.match(pattern, username) is not None

def hash_password(password):
    """Хеширование пароля с солью"""
    # В продакшене используйте bcrypt
    salt = 'eimors_music_salt_2024'  # В продакшене используйте уникальную соль
    return hashlib.sha256((password + salt).encode()).hexdigest()

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect('eimors.db')
    c = conn.cursor()
    
    # Таблица пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        name TEXT NOT NULL,
        username TEXT UNIQUE,
        avatar TEXT,
        is_admin INTEGER DEFAULT 0,
        is_premium INTEGER DEFAULT 0,
        premium_until TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Таблица треков
    c.execute('''CREATE TABLE IF NOT EXISTS tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        emoji TEXT DEFAULT '🎵',
        cover_path TEXT,
        audio_path TEXT,
        album_id INTEGER,
        duration TEXT,
        description TEXT,
        plays INTEGER DEFAULT 0,
        is_premium_early INTEGER DEFAULT 0,
        premium_release_date TIMESTAMP,
        public_release_date TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE SET NULL
    )''')
    
    # Таблица альбомов
    c.execute('''CREATE TABLE IF NOT EXISTS albums (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        emoji TEXT DEFAULT '💿',
        cover_path TEXT,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Таблица сохраненных треков
    c.execute('''CREATE TABLE IF NOT EXISTS saved_tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        track_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE,
        UNIQUE(user_id, track_id)
    )''')
    
    # Таблиця постів преміум-каналу
    c.execute('''CREATE TABLE IF NOT EXISTS premium_channel_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        post_type TEXT NOT NULL,
        file_path TEXT,
        caption TEXT,
        track_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE SET NULL
    )''')

    # Индексы для оптимизации
    c.execute('CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_saved_tracks_user ON saved_tracks(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_saved_tracks_track ON saved_tracks(track_id)')
    
    # Создаем папки для загрузок
    for folder in ['covers', 'music', 'albums', 'avatars']:
        os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], folder), exist_ok=True)
    
    # Создаем админа если его нет
    admin_email = os.environ.get('ADMIN_EMAIL', 'eimorsmusic@gmail.com')
    admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
    
    c.execute("SELECT * FROM users WHERE email = ?", (admin_email,))
    if not c.fetchone():
        password_hash = hash_password(admin_password)
        c.execute("INSERT INTO users (email, password, name, username, is_admin) VALUES (?, ?, ?, ?, ?)",
                 (admin_email, password_hash, 'Admin', 'admin', 1))
        print(f"✅ Создан админ аккаунт: {admin_email}")
    
    # Добавляем тестовые треки только в dev режиме
    if os.environ.get('FLASK_ENV') != 'production':
        c.execute("SELECT COUNT(*) FROM tracks")
        if c.fetchone()[0] == 0:
            test_tracks = [
                ('Midnight Dreams', '🌙', None, None, None, '3:45', 'Ночная музыка', 1250),
                ('Electric Soul', '⚡', None, None, None, '4:12', 'Электронная энергия', 890),
                ('Sunset Vibes', '🌅', None, None, None, '3:30', 'Закатное настроение', 654)
            ]
            c.executemany('''INSERT INTO tracks 
                (title, emoji, cover_path, audio_path, album_id, duration, description, plays)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', test_tracks)
            print("✅ Добавлены тестовые треки")
    
    conn.commit()
    conn.close()

def allowed_file(filename, allowed_extensions):
    """Проверка допустимости файла"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

def update_last_seen(user_id):
    """Обновление времени последней активности"""
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute("UPDATE users SET last_seen = ? WHERE id = ?", (datetime.now(), user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error updating last_seen: {e}")

def get_online_count():
    """Подсчет пользователей онлайн (активность за последние 5 минут)"""
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        five_minutes_ago = datetime.now() - timedelta(minutes=5)
        c.execute("SELECT COUNT(*) FROM users WHERE last_seen > ?", (five_minutes_ago,))
        count = c.fetchone()[0]
        conn.close()
        return count
    except:
        return 0

# ============ API ROUTES ============

@app.route('/')
def index():
    """Главная страница"""
    try:
        # Ищем index.html в текущей директории
        if os.path.exists('index.html'):
            return send_from_directory('.', 'index.html')
        # Если не найден, пробуем в родительской директории
        elif os.path.exists('../index.html'):
            return send_from_directory('..', 'index.html')
        else:
            return "EIMOR MUSIC API v1.0 - Server is running. Please place index.html in the app directory.", 200
    except Exception as e:
        print(f"Error serving index.html: {e}")
        return "EIMOR MUSIC API v1.0 - Server is running", 200

# ============ АВТОРИЗАЦИЯ ============

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Вход в систему"""
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({'success': False, 'message': 'Email та пароль обов\'язкові'}), 400
    
    if not validate_email(email):
        return jsonify({'success': False, 'message': 'Некоректний email'}), 400
    
    password_hash = hash_password(password)
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute("""SELECT id, email, name, username, avatar, is_admin, is_premium, premium_until 
                     FROM users WHERE email = ? AND password = ?""",
                 (email, password_hash))
        user = c.fetchone()
        
        if user:
            # Обновляем last_seen
            c.execute("UPDATE users SET last_seen = ? WHERE id = ?", (datetime.now(), user[0]))
            conn.commit()
            
            # Проверяем актуальность премиума
            is_premium = bool(user[6])
            premium_until = user[7]
            
            if is_premium and premium_until:
                premium_date = datetime.fromisoformat(premium_until)
                if premium_date < datetime.now():
                    # Премиум истек
                    c.execute("UPDATE users SET is_premium = 0 WHERE id = ?", (user[0],))
                    conn.commit()
                    is_premium = False
                    premium_until = None
            
            user_data = {
                'id': user[0],
                'email': user[1],
                'name': user[2],
                'username': user[3],
                'avatar': user[4],
                'isAdmin': bool(user[5]),
                'isPremium': is_premium,
                'premiumUntil': premium_until
            }
            
            conn.close()
            return jsonify({'success': True, 'user': user_data})
        
        conn.close()
        return jsonify({'success': False, 'message': 'Невірний email або пароль'}), 401
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'success': False, 'message': 'Помилка сервера'}), 500

@app.route('/api/profile/update', methods=['POST'])
def update_profile():
    """Обновление профиля пользователя"""
    user_id = request.form.get('user_id')
    name = request.form.get('name', '').strip()
    username = request.form.get('username', '').strip()
    
    if not user_id or not name:
        return jsonify({'success': False, 'message': 'User ID та ім\'я обов\'язкові'}), 400
    
    if username and not validate_username(username):
        return jsonify({'success': False, 'message': 'Некоректний username (3-20 символів, літери, цифри, _)'}), 400
    
    avatar_path = None
    
    # Обработка загрузки аватара
    if 'avatar' in request.files:
        file = request.files['avatar']
        if file and file.filename and allowed_file(file.filename, ALLOWED_IMAGES):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"avatar_{user_id}_{timestamp}_{filename}"
            avatar_path = os.path.join('avatars', filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], avatar_path))
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        
        # Проверяем, существует ли username у другого пользователя (case-insensitive)
        if username:
            c.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?) AND id != ?", (username, user_id))
            if c.fetchone():
                conn.close()
                return jsonify({'success': False, 'message': 'Username вже зайнятий'}), 400
        
        # Получаем старый аватар для удаления
        if avatar_path:
            c.execute("SELECT avatar FROM users WHERE id = ?", (user_id,))
            old_avatar = c.fetchone()
            if old_avatar and old_avatar[0]:
                try:
                    os.remove(os.path.join(app.config['UPLOAD_FOLDER'], old_avatar[0]))
                except:
                    pass
        
        # Обновляем профиль
        if avatar_path:
            c.execute("""UPDATE users SET name = ?, username = ?, avatar = ? 
                        WHERE id = ?""", (name, username if username else None, avatar_path, user_id))
        else:
            c.execute("""UPDATE users SET name = ?, username = ? 
                        WHERE id = ?""", (name, username if username else None, user_id))
        
        conn.commit()
        
        # Получаем обновленные данные пользователя
        c.execute("SELECT id, email, name, username, avatar, is_admin, is_premium, premium_until FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()
        
        user_data = {
            'id': user[0],
            'email': user[1],
            'name': user[2],
            'username': user[3],
            'avatar': user[4],
            'isAdmin': bool(user[5]),
            'isPremium': bool(user[6]),
            'premiumUntil': user[7]
        }
        
        conn.close()
        return jsonify({'success': True, 'user': user_data})
    except Exception as e:
        print(f"Update profile error: {e}")
        if avatar_path:
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], avatar_path))
            except:
                pass
        return jsonify({'success': False, 'message': 'Помилка оновлення профілю'}), 500

@app.route('/api/auth/register', methods=['POST'])
def register():
    """Регистрация нового пользователя"""
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    name = data.get('name', '').strip()
    username = data.get('username', '').strip()
    
    # Валидация
    if not email or not password or not name:
        return jsonify({'success': False, 'message': 'Email, пароль та ім\'я обов\'язкові'}), 400
    
    if not validate_email(email):
        return jsonify({'success': False, 'message': 'Некоректний email'}), 400
    
    if len(password) < 6:
        return jsonify({'success': False, 'message': 'Пароль має бути мінімум 6 символів'}), 400
    
    if username and not validate_username(username):
        return jsonify({'success': False, 'message': 'Username має бути 3-20 символів (літери, цифри, _)'}), 400
    
    if not username:
        username = None
    
    password_hash = hash_password(password)
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        
        # Перевірка чи існує username (case-insensitive)
        if username:
            c.execute("SELECT username FROM users WHERE LOWER(username) = LOWER(?)", (username,))
            if c.fetchone():
                conn.close()
                return jsonify({'success': False, 'message': 'Це ім\'я користувача вже зайняте'}), 400
        
        c.execute("INSERT INTO users (email, password, name, username, last_seen) VALUES (?, ?, ?, ?, ?)",
                 (email, password_hash, name, username, datetime.now()))
        user_id = c.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'user': {
                'id': user_id,
                'email': email,
                'name': name,
                'username': username,
                'avatar': None,
                'isAdmin': False,
                'isPremium': False,
                'premiumUntil': None
            }
        })
    except sqlite3.IntegrityError as e:
        error_msg = str(e)
        if 'username' in error_msg:
            return jsonify({'success': False, 'message': 'Це ім\'я користувача вже зайняте'}), 400
        else:
            return jsonify({'success': False, 'message': 'Email вже зареєстрований'}), 400
    except Exception as e:
        print(f"Register error: {e}")
        return jsonify({'success': False, 'message': 'Помилка сервера'}), 500

# ============ ПОЛЬЗОВАТЕЛИ ============

@app.route('/api/users/<int:user_id>', methods=['PUT'])
def update_user(user_id):
    """Обновление профиля пользователя"""
    name = request.form.get('name', '').strip()
    username = request.form.get('username', '').strip()
    
    if not name:
        return jsonify({'success': False, 'message': 'Имя обязательно'}), 400
    
    if username and not validate_username(username):
        return jsonify({'success': False, 'message': 'Username должен быть 3-20 символов'}), 400
    
    if not username:
        username = None
    
    avatar_path = None
    
    # Сохраняем аватар
    if 'avatar' in request.files:
        avatar = request.files['avatar']
        if avatar and avatar.filename and allowed_file(avatar.filename, ALLOWED_IMAGES):
            # Безопасное имя файла
            ext = avatar.filename.rsplit('.', 1)[1].lower()
            filename = f"{user_id}_{datetime.now().timestamp()}.{ext}"
            avatar_path = f"avatars/{filename}"
            
            # Удаляем старый аватар
            try:
                conn = sqlite3.connect('eimors.db')
                c = conn.cursor()
                c.execute("SELECT avatar FROM users WHERE id = ?", (user_id,))
                old_avatar = c.fetchone()
                if old_avatar and old_avatar[0]:
                    old_path = os.path.join(app.config['UPLOAD_FOLDER'], old_avatar[0])
                    if os.path.exists(old_path):
                        os.remove(old_path)
                conn.close()
            except:
                pass
            
            avatar.save(os.path.join(app.config['UPLOAD_FOLDER'], avatar_path))
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        
        if avatar_path:
            c.execute("UPDATE users SET name = ?, username = ?, avatar = ? WHERE id = ?",
                     (name, username, avatar_path, user_id))
        else:
            c.execute("UPDATE users SET name = ?, username = ? WHERE id = ?",
                     (name, username, user_id))
        
        conn.commit()
        
        # Получаем обновленные данные
        c.execute("SELECT id, email, name, username, avatar, is_admin FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()
        conn.close()
        
        if user:
            return jsonify({
                'success': True,
                'user': {
                    'id': user[0],
                    'email': user[1],
                    'name': user[2],
                    'username': user[3],
                    'avatar': user[4],
                    'isAdmin': bool(user[5])
                }
            })
        
        return jsonify({'success': False, 'message': 'Пользователь не найден'}), 404
        
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': 'Это имя пользователя уже занято'}), 400
    except Exception as e:
        print(f"Update user error: {e}")
        return jsonify({'success': False, 'message': 'Ошибка сервера'}), 500

# ============ ТРЕКИ ============

@app.route('/api/tracks', methods=['GET'])
def get_tracks():
    """Получить все треки з врахуванням преміум доступу"""
    user_id = request.args.get('user_id')
    
    try:
        conn = sqlite3.connect('eimors.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Перевіряємо чи користувач має преміум
        is_premium = False
        if user_id:
            c.execute("SELECT is_premium, premium_until FROM users WHERE id = ?", (user_id,))
            user = c.fetchone()
            if user and user[0]:
                premium_until = user[1]
                if premium_until:
                    premium_date = datetime.fromisoformat(premium_until)
                    if premium_date >= datetime.now():
                        is_premium = True
        
        # Отримуємо треки
        c.execute("SELECT * FROM tracks ORDER BY created_at DESC")
        all_tracks = [dict(row) for row in c.fetchall()]
        
        # Фільтруємо треки для не-преміум користувачів
        tracks = []
        current_time = datetime.now()
        
        for track in all_tracks:
            # Якщо трек не має раннього доступу - показуємо всім
            if not track.get('is_premium_early'):
                tracks.append(track)
                continue
            
            # Якщо є ранній доступ
            public_date = track.get('public_release_date')
            
            # Преміум користувачі бачать все
            if is_premium:
                tracks.append(track)
            # Не-преміум бачать тільки якщо вийшла публічна дата
            elif public_date:
                public_datetime = datetime.fromisoformat(public_date)
                if public_datetime <= current_time:
                    tracks.append(track)
            # Якщо немає публічної дати і немає преміуму - не показуємо
        
        conn.close()
        return jsonify(tracks)
    except Exception as e:
        print(f"Get tracks error: {e}")
        return jsonify({'error': 'Помилка завантаження треків'}), 500

@app.route('/api/tracks/<int:track_id>', methods=['GET'])
def get_track(track_id):
    """Получить один трек"""
    try:
        conn = sqlite3.connect('eimors.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM tracks WHERE id = ?", (track_id,))
        track = c.fetchone()
        conn.close()
        
        if track:
            return jsonify(dict(track))
        return jsonify({'error': 'Трек не найден'}), 404
    except Exception as e:
        print(f"Get track error: {e}")
        return jsonify({'error': 'Ошибка загрузки трека'}), 500

@app.route('/api/tracks', methods=['POST'])
def create_track():
    """Создать новый трек"""
    title = request.form.get('title', '').strip()
    emoji = request.form.get('emoji', '🎵')
    duration = request.form.get('duration', '')
    description = request.form.get('description', '')
    album_id = request.form.get('album_id') or None
    is_premium_early = request.form.get('is_premium_early', '0')
    days_early = request.form.get('days_early', '7')  # За замовчуванням 7 днів раніше
    
    if not title:
        return jsonify({'success': False, 'message': 'Назва треку обов\'язкова'}), 400
    
    cover_path = None
    audio_path = None
    
    # Сохраняем обложку
    if 'cover' in request.files:
        cover = request.files['cover']
        if cover and cover.filename and allowed_file(cover.filename, ALLOWED_IMAGES):
            ext = cover.filename.rsplit('.', 1)[1].lower()
            filename = f"cover_{datetime.now().timestamp()}.{ext}"
            cover_path = f"covers/{filename}"
            cover.save(os.path.join(app.config['UPLOAD_FOLDER'], cover_path))
    
    # Сохраняем аудио
    if 'audio' in request.files:
        audio = request.files['audio']
        if audio and audio.filename and allowed_file(audio.filename, ALLOWED_AUDIO):
            ext = audio.filename.rsplit('.', 1)[1].lower()
            filename = f"track_{datetime.now().timestamp()}.{ext}"
            audio_path = f"music/{filename}"
            audio.save(os.path.join(app.config['UPLOAD_FOLDER'], audio_path))
    else:
        return jsonify({'success': False, 'message': 'Аудіо файл обов\'язковий'}), 400
    
    # Розрахунок дат випуску
    premium_release_date = datetime.now()
    public_release_date = None
    
    if is_premium_early == '1':
        try:
            days = int(days_early)
            public_release_date = premium_release_date + timedelta(days=days)
        except:
            days = 7
            public_release_date = premium_release_date + timedelta(days=7)
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute('''INSERT INTO tracks (title, emoji, cover_path, audio_path, album_id, duration, description,
                     is_premium_early, premium_release_date, public_release_date)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (title, emoji, cover_path, audio_path, album_id, duration, description,
                  1 if is_premium_early == '1' else 0,
                  premium_release_date if is_premium_early == '1' else None,
                  public_release_date if is_premium_early == '1' else None))
        track_id = c.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'id': track_id})
    except Exception as e:
        print(f"Create track error: {e}")
        # Удаляем загруженные файлы при ошибке
        if cover_path:
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], cover_path))
            except:
                pass
        if audio_path:
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], audio_path))
            except:
                pass
        return jsonify({'success': False, 'message': 'Помилка створення треку'}), 500

@app.route('/api/tracks/<int:track_id>', methods=['DELETE'])
def delete_track(track_id):
    """Удалить трек"""
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        
        # Получаем пути к файлам
        c.execute("SELECT cover_path, audio_path FROM tracks WHERE id = ?", (track_id,))
        track = c.fetchone()
        
        if track:
            # Удаляем файлы
            for file_path in [track[0], track[1]]:
                if file_path:
                    try:
                        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], file_path))
                    except:
                        pass
            
            # Удаляем из БД
            c.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            conn.commit()
        
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Delete track error: {e}")
        return jsonify({'success': False, 'message': 'Ошибка удаления трека'}), 500

@app.route('/api/tracks/<int:track_id>/play', methods=['POST'])
def play_track(track_id):
    """Увеличить счетчик прослушиваний"""
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute("UPDATE tracks SET plays = plays + 1 WHERE id = ?", (track_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Play track error: {e}")
        return jsonify({'success': False}), 500

# ============ АЛЬБОМЫ ============

@app.route('/api/albums', methods=['GET'])
def get_albums():
    """Получить все альбомы"""
    try:
        conn = sqlite3.connect('eimors.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM albums ORDER BY created_at DESC")
        albums = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify(albums)
    except Exception as e:
        print(f"Get albums error: {e}")
        return jsonify({'error': 'Ошибка загрузки альбомов'}), 500

@app.route('/api/albums', methods=['POST'])
def create_album():
    """Создать новый альбом"""
    title = request.form.get('title', '').strip()
    emoji = request.form.get('emoji', '💿')
    description = request.form.get('description', '')
    
    if not title:
        return jsonify({'success': False, 'message': 'Название альбома обязательно'}), 400
    
    cover_path = None
    if 'cover' in request.files:
        cover = request.files['cover']
        if cover and cover.filename and allowed_file(cover.filename, ALLOWED_IMAGES):
            ext = cover.filename.rsplit('.', 1)[1].lower()
            filename = f"album_{datetime.now().timestamp()}.{ext}"
            cover_path = f"albums/{filename}"
            cover.save(os.path.join(app.config['UPLOAD_FOLDER'], cover_path))
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute("INSERT INTO albums (title, emoji, cover_path, description) VALUES (?, ?, ?, ?)",
                 (title, emoji, cover_path, description))
        album_id = c.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'id': album_id})
    except Exception as e:
        print(f"Create album error: {e}")
        if cover_path:
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], cover_path))
            except:
                pass
        return jsonify({'success': False, 'message': 'Ошибка создания альбома'}), 500

@app.route('/api/albums/<int:album_id>', methods=['DELETE'])
def delete_album(album_id):
    """Удалить альбом"""
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        
        # Получаем путь к обложке
        c.execute("SELECT cover_path FROM albums WHERE id = ?", (album_id,))
        album = c.fetchone()
        
        if album and album[0]:
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], album[0]))
            except:
                pass
        
        # Обнуляем album_id у треков
        c.execute("UPDATE tracks SET album_id = NULL WHERE album_id = ?", (album_id,))
        c.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Delete album error: {e}")
        return jsonify({'success': False, 'message': 'Ошибка удаления альбома'}), 500

# ============ СТАТИСТИКА ============

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Получить статистику"""
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM tracks")
        tracks_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM albums")
        albums_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE is_admin = 0")
        users_count = c.fetchone()[0]
        
        c.execute("SELECT SUM(plays) FROM tracks")
        total_plays = c.fetchone()[0] or 0
        
        conn.close()
        
        online = get_online_count()
        
        return jsonify({
            'tracks': tracks_count,
            'albums': albums_count,
            'users': users_count,
            'plays': total_plays,
            'online': online
        })
    except Exception as e:
        print(f"Get stats error: {e}")
        return jsonify({'error': 'Ошибка получения статистики'}), 500

@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    """Обновление активности пользователя"""
    data = request.json
    user_id = data.get('user_id')
    
    if user_id:
        update_last_seen(user_id)
        return jsonify({'success': True})
    
    return jsonify({'success': False}), 400

# ============ ЗБЕРЕЖЕНІ ТРЕКИ ============

@app.route('/api/saved-tracks', methods=['GET'])
def get_saved_tracks():
    """Получить сохраненные треки пользователя"""
    user_id = request.args.get('user_id')
    
    if not user_id:
        return jsonify({'success': False, 'message': 'User ID required'}), 400
    
    try:
        conn = sqlite3.connect('eimors.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute('''
            SELECT t.*, s.created_at as saved_at 
            FROM tracks t
            INNER JOIN saved_tracks s ON t.id = s.track_id
            WHERE s.user_id = ?
            ORDER BY s.created_at DESC
        ''', (user_id,))
        
        tracks = [dict(row) for row in c.fetchall()]
        conn.close()
        
        return jsonify({'success': True, 'tracks': tracks})
    except Exception as e:
        print(f"Get saved tracks error: {e}")
        return jsonify({'success': False, 'message': 'Ошибка получения треков'}), 500

@app.route('/api/saved-tracks', methods=['POST'])
def save_track():
    """Сохранить трек"""
    data = request.json
    user_id = data.get('user_id')
    track_id = data.get('track_id')
    
    if not user_id or not track_id:
        return jsonify({'success': False, 'message': 'User ID и Track ID обязательны'}), 400
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        
        # Проверяем, не сохранен ли уже
        c.execute("SELECT id FROM saved_tracks WHERE user_id = ? AND track_id = ?", (user_id, track_id))
        if c.fetchone():
            conn.close()
            return jsonify({'success': False, 'message': 'Трек уже сохранен'}), 400
        
        c.execute("INSERT INTO saved_tracks (user_id, track_id) VALUES (?, ?)", (user_id, track_id))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Save track error: {e}")
        return jsonify({'success': False, 'message': 'Ошибка сохранения трека'}), 500

@app.route('/api/saved-tracks/<int:track_id>', methods=['DELETE'])
def unsave_track(track_id):
    """Удалить трек из сохраненных"""
    user_id = request.args.get('user_id')
    
    if not user_id:
        return jsonify({'success': False, 'message': 'User ID required'}), 400
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute("DELETE FROM saved_tracks WHERE user_id = ? AND track_id = ?", (user_id, track_id))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Unsave track error: {e}")
        return jsonify({'success': False, 'message': 'Ошибка удаления трека'}), 500

@app.route('/api/saved-tracks/check', methods=['GET'])
def check_saved_track():
    """Проверить, сохранен ли трек"""
    user_id = request.args.get('user_id')
    track_id = request.args.get('track_id')
    
    if not user_id or not track_id:
        return jsonify({'success': False, 'saved': False}), 400
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute("SELECT id FROM saved_tracks WHERE user_id = ? AND track_id = ?", (user_id, track_id))
        saved = c.fetchone() is not None
        conn.close()
        
        return jsonify({'success': True, 'saved': saved})
    except Exception as e:
        print(f"Check saved track error: {e}")
        return jsonify({'success': False, 'saved': False}), 500

@app.route('/api/users/search', methods=['GET'])
def search_user():
    """Пошук користувача по нікнейму (case-insensitive)"""
    username = request.args.get('username', '').strip()
    
    if not username:
        return jsonify({'success': False, 'message': 'Username обов''язковий'}), 400
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute("""SELECT id, email, name, username, avatar, is_admin, is_premium, premium_until 
                     FROM users WHERE LOWER(username) = LOWER(?)""", (username,))
        user = c.fetchone()
        conn.close()
        
        if user:
            is_premium = bool(user[6])
            premium_until = user[7]
            
            if is_premium and premium_until:
                premium_date = datetime.fromisoformat(premium_until)
                if premium_date < datetime.now():
                    is_premium = False
                    premium_until = None
            
            user_data = {
                'id': user[0],
                'email': user[1],
                'name': user[2],
                'username': user[3],
                'avatar': user[4],
                'isAdmin': bool(user[5]),
                'isPremium': is_premium,
                'premiumUntil': premium_until
            }
            
            return jsonify({'success': True, 'user': user_data})
        
        return jsonify({'success': False, 'message': 'Користувача не знайдено'}), 404
    except Exception as e:
        print(f"Search user error: {e}")
        return jsonify({'success': False, 'message': 'Помилка сервера'}), 500

# ============ ПРЕМИУМ ============

@app.route('/api/premium/grant', methods=['POST'])
def grant_premium():
    """Видача преміуму користувачу (тільки для адмінів)"""
    data = request.json
    admin_id = data.get('admin_id')
    username = data.get('username', '').strip()
    duration_type = data.get('duration_type', 'month')  # 'month', 'year', або 'forever'
    
    if not admin_id or not username:
        return jsonify({'success': False, 'message': 'Admin ID та username обов\'язкові'}), 400
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        
        # Перевірка, що запит від адміна
        c.execute("SELECT is_admin FROM users WHERE id = ?", (admin_id,))
        admin = c.fetchone()
        if not admin or not admin[0]:
            conn.close()
            return jsonify({'success': False, 'message': 'Доступ заборонено'}), 403
        
        # Знаходимо користувача по username (case-insensitive)
        c.execute("SELECT id, is_premium, premium_until FROM users WHERE LOWER(username) = LOWER(?)", (username,))
        user = c.fetchone()
        
        if not user:
            conn.close()
            return jsonify({'success': False, 'message': f'Користувача з ніком "{username}" не знайдено'}), 404
        
        user_id = user[0]
        
        # Розрахунок дати закінчення преміуму
        if duration_type == 'forever':
            premium_until = datetime.now() + timedelta(days=36500)
        elif duration_type == 'year':
            premium_until = datetime.now() + timedelta(days=365)
        else:  # month
            premium_until = datetime.now() + timedelta(days=30)
        
        # Оновлення статусу преміуму
        c.execute("""UPDATE users 
                     SET is_premium = 1, premium_until = ? 
                     WHERE id = ?""", (premium_until, user_id))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Преміум видано користувачу {username} до {premium_until.strftime("%Y-%m-%d %H:%M")}',
            'premiumUntil': premium_until.isoformat()
        })
    except Exception as e:
        print(f"Grant premium error: {e}")
        return jsonify({'success': False, 'message': 'Помилка видачі преміуму'}), 500

@app.route('/api/premium/revoke', methods=['POST'])
def revoke_premium():
    """Відібрати преміум у користувача (тільки для адмінів)"""
    data = request.json
    admin_id = data.get('admin_id')
    username = data.get('username', '').strip()
    
    if not admin_id or not username:
        return jsonify({'success': False, 'message': 'Admin ID та username обов\'язкові'}), 400
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        
        # Перевірка, що запит від адміна
        c.execute("SELECT is_admin FROM users WHERE id = ?", (admin_id,))
        admin = c.fetchone()
        if not admin or not admin[0]:
            conn.close()
            return jsonify({'success': False, 'message': 'Доступ заборонено'}), 403
        
        # Знаходимо користувача
        c.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,))
        user = c.fetchone()
        
        if not user:
            conn.close()
            return jsonify({'success': False, 'message': f'Користувача з ніком "{username}" не знайдено'}), 404
        
        user_id = user[0]
        
        # Відбираємо преміум
        c.execute("""UPDATE users 
                     SET is_premium = 0, premium_until = NULL 
                     WHERE id = ?""", (user_id,))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Преміум відібрано у користувача {username}'
        })
    except Exception as e:
        print(f"Revoke premium error: {e}")
        return jsonify({'success': False, 'message': 'Помилка відбору преміуму'}), 500

@app.route('/api/premium/check', methods=['GET'])
def check_premium():
    """Перевірка статусу преміуму користувача"""
    user_id = request.args.get('user_id')
    
    if not user_id:
        return jsonify({'success': False, 'isPremium': False}), 400
    
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute("SELECT is_premium, premium_until FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()
        conn.close()
        
        if not user:
            return jsonify({'success': False, 'isPremium': False}), 404
        
        is_premium = bool(user[0])
        premium_until = user[1]
        
        # Перевірка актуальності
        if is_premium and premium_until:
            premium_date = datetime.fromisoformat(premium_until)
            if premium_date < datetime.now():
                is_premium = False
                premium_until = None
        
        return jsonify({
            'success': True,
            'isPremium': is_premium,
            'premiumUntil': premium_until
        })
    except Exception as e:
        print(f"Check premium error: {e}")
        return jsonify({'success': False, 'isPremium': False}), 500

ALLOWED_VIDEO = {'mp4', 'mov', 'webm'}

# ============ ПРЕМІУМ КАНАЛ (фото, кружочки, відрізки, голосові) ============

@app.route('/api/premium/posts', methods=['GET'])
def get_premium_posts():
    """Отримати пости преміум-каналу (тільки для преміум/адмінів)"""
    user_id = request.args.get('user_id')

    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()

        if user_id:
            c.execute("SELECT is_premium, is_admin, premium_until FROM users WHERE id = ?", (user_id,))
            user = c.fetchone()
            if not user:
                conn.close()
                return jsonify({'success': False, 'message': 'Доступ заборонено'}), 403
            is_premium_user = bool(user[0])
            is_admin_user = bool(user[1])
            premium_until = user[2]
            # перевірити термін
            if is_premium_user and premium_until:
                if datetime.fromisoformat(premium_until) < datetime.now():
                    is_premium_user = False
            if not is_premium_user and not is_admin_user:
                conn.close()
                return jsonify({'success': False, 'message': 'Потрібен преміум для перегляду'}), 403
        else:
            conn.close()
            return jsonify({'success': False, 'message': 'Потрібна авторизація'}), 401

        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM premium_channel_posts ORDER BY created_at DESC")
        posts = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify({'success': True, 'posts': posts})
    except Exception as e:
        print(f"Get premium posts error: {e}")
        return jsonify({'success': False, 'message': 'Помилка сервера'}), 500


@app.route('/api/premium/posts', methods=['POST'])
def create_premium_post():
    """Адмін публікує пост у преміум-канал"""
    admin_id = request.form.get('admin_id')
    post_type = request.form.get('post_type')   # photo | video_note | audio_snippet | voice | text
    caption = request.form.get('caption', '')
    track_id = request.form.get('track_id') or None

    if not admin_id:
        return jsonify({'success': False, 'message': 'Admin ID обов\'язковий'}), 400

    # Перевірка адміна
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute("SELECT is_admin FROM users WHERE id = ?", (admin_id,))
        u = c.fetchone()
        conn.close()
        if not u or not u[0]:
            return jsonify({'success': False, 'message': 'Доступ заборонено'}), 403
    except Exception as e:
        return jsonify({'success': False, 'message': 'Помилка перевірки'}), 500

    valid_types = {'photo', 'video_note', 'audio_snippet', 'voice', 'text'}
    if post_type not in valid_types:
        return jsonify({'success': False, 'message': f'Тип має бути: {", ".join(valid_types)}'}), 400

    file_path = None

    if post_type == 'photo' and 'file' in request.files:
        f = request.files['file']
        if f and f.filename and allowed_file(f.filename, ALLOWED_IMAGES):
            ext = f.filename.rsplit('.', 1)[1].lower()
            filename = f"premium_photo_{datetime.now().timestamp()}.{ext}"
            file_path = f"premium/photos/{filename}"
            os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'premium', 'photos'), exist_ok=True)
            f.save(os.path.join(app.config['UPLOAD_FOLDER'], file_path))

    elif post_type == 'video_note' and 'file' in request.files:
        f = request.files['file']
        if f and f.filename:
            fname = f.filename or ''
            if '.' in fname:
                ext = fname.rsplit('.', 1)[1].lower()
            else:
                ct = f.content_type or ''
                ext_map = {'video/webm': 'webm', 'video/mp4': 'mp4', 'video/quicktime': 'mov',
                           'application/octet-stream': 'webm'}
                ext = ext_map.get(ct.split(';')[0].strip(), 'webm')
            
            if ext in ALLOWED_VIDEO or ext == 'webm':
                filename = f"premium_circle_{datetime.now().timestamp()}.{ext}"
                file_path = f"premium/circles/{filename}"
                os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'premium', 'circles'), exist_ok=True)
                f.save(os.path.join(app.config['UPLOAD_FOLDER'], file_path))

    elif post_type in ('audio_snippet', 'voice') and 'file' in request.files:
        f = request.files['file']
        if f and f.filename:
            # Визначаємо розширення з filename або content_type
            fname = f.filename or ''
            if '.' in fname:
                ext = fname.rsplit('.', 1)[1].lower()
            else:
                # Fallback по content-type
                ct = f.content_type or ''
                ext_map = {'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mpeg': 'mp3',
                           'audio/mp4': 'm4a', 'audio/wav': 'wav', 'video/webm': 'webm',
                           'application/octet-stream': 'webm'}
                ext = ext_map.get(ct.split(';')[0].strip(), 'webm')
            
            if ext in ALLOWED_AUDIO or ext in ALLOWED_VIDEO:
                filename = f"premium_{post_type}_{datetime.now().timestamp()}.{ext}"
                file_path = f"premium/audio/{filename}"
                os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'premium', 'audio'), exist_ok=True)
                f.save(os.path.join(app.config['UPLOAD_FOLDER'], file_path))

    elif post_type == 'text':
        if not caption.strip():
            return jsonify({'success': False, 'message': 'Текст не може бути порожнім'}), 400

    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute(
            "INSERT INTO premium_channel_posts(admin_id, post_type, file_path, caption, track_id) VALUES(?,?,?,?,?)",
            (admin_id, post_type, file_path, caption, track_id)
        )
        post_id = c.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': post_id})
    except Exception as e:
        print(f"Create premium post error: {e}")
        return jsonify({'success': False, 'message': 'Помилка публікації'}), 500


@app.route('/api/premium/posts/<int:post_id>', methods=['DELETE'])
def delete_premium_post(post_id):
    """Видалити пост з преміум-каналу"""
    admin_id = request.args.get('admin_id')
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute("SELECT is_admin FROM users WHERE id = ?", (admin_id,))
        u = c.fetchone()
        if not u or not u[0]:
            conn.close()
            return jsonify({'success': False, 'message': 'Доступ заборонено'}), 403

        c.execute("SELECT file_path FROM premium_channel_posts WHERE id = ?", (post_id,))
        post = c.fetchone()
        if post and post[0]:
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], post[0]))
            except:
                pass

        c.execute("DELETE FROM premium_channel_posts WHERE id = ?", (post_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Delete premium post error: {e}")
        return jsonify({'success': False, 'message': 'Помилка видалення'}), 500


# ============ НАЛАШТУВАННЯ РЕЛІЗУ ТРЕКУ (негайно / через N днів) ============

@app.route('/api/tracks/<int:track_id>/release', methods=['POST'])
def set_track_release(track_id):
    """Адмін налаштовує режим релізу треку"""
    data = request.json
    admin_id = data.get('admin_id')
    release_mode = data.get('release_mode', 'immediate')   # immediate | delayed
    days_delay = int(data.get('days_delay', 7))

    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()

        c.execute("SELECT is_admin FROM users WHERE id = ?", (admin_id,))
        u = c.fetchone()
        if not u or not u[0]:
            conn.close()
            return jsonify({'success': False, 'message': 'Доступ заборонено'}), 403

        now = datetime.now()

        if release_mode == 'immediate':
            # Трек доступний всім зразу
            c.execute(
                "UPDATE tracks SET is_premium_early=0, premium_release_date=NULL, public_release_date=NULL WHERE id=?",
                (track_id,)
            )
            msg = 'Трек опубліковано негайно для всіх'
        else:
            # Преміум отримує зараз, решта — через days_delay днів
            public_date = now + timedelta(days=days_delay)
            c.execute(
                """UPDATE tracks SET is_premium_early=1,
                   premium_release_date=?, public_release_date=? WHERE id=?""",
                (now.isoformat(), public_date.isoformat(), track_id)
            )
            msg = f'Преміум отримає зараз. Публічний реліз — через {days_delay} дн. ({public_date.strftime("%d.%m.%Y")})'

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        print(f"Set track release error: {e}")
        return jsonify({'success': False, 'message': 'Помилка налаштування'}), 500


@app.route('/api/ads/config', methods=['GET'])
def get_ads_config():
    """Отримання конфігурації реклами (показувати чи ні)"""
    user_id = request.args.get('user_id')
    
    show_ads = True  # За замовчуванням показувати рекламу
    
    if user_id:
        try:
            conn = sqlite3.connect('eimors.db')
            c = conn.cursor()
            c.execute("SELECT is_premium, premium_until FROM users WHERE id = ?", (user_id,))
            user = c.fetchone()
            conn.close()
            
            if user:
                is_premium = bool(user[0])
                premium_until = user[1]
                
                # Якщо є преміум і він актуальний - не показувати рекламу
                if is_premium and premium_until:
                    premium_date = datetime.fromisoformat(premium_until)
                    if premium_date >= datetime.now():
                        show_ads = False
        except Exception as e:
            print(f"Get ads config error: {e}")
    
    return jsonify({
        'success': True,
        'showAds': show_ads,
        'adBlocks': [
            {
                'id': 'sidebar_top',
                'position': 'sidebar',
                'enabled': show_ads
            },
            {
                'id': 'player_banner',
                'position': 'player',
                'enabled': show_ads
            },
            {
                'id': 'track_list_banner',
                'position': 'tracklist',
                'enabled': show_ads
            }
        ]
    })

# ============ ФАЙЛЫ ============

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    """Отдача загруженных файлов с правильными MIME типами"""
    import mimetypes
    mimetypes.add_type('audio/webm', '.webm')
    mimetypes.add_type('video/webm', '.webm')
    mimetypes.add_type('audio/ogg', '.ogg')
    upload_dir = os.path.abspath(app.config['UPLOAD_FOLDER'])
    response = send_from_directory(upload_dir, filename)
    # Дозволяємо кешування але з перевіркою — аватарки/обкладинки оновлюються рідко
    response.headers['Cache-Control'] = 'public, max-age=300, must-revalidate'
    response.headers['Vary'] = 'Accept-Encoding'
    return response

@app.route('/api/users/me', methods=['GET'])
def get_current_user():
    """Отримати актуальні дані поточного користувача (для синхронізації між пристроями)"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'User ID required'}), 400
    try:
        conn = sqlite3.connect('eimors.db')
        c = conn.cursor()
        c.execute("""SELECT id, email, name, username, avatar, is_admin, is_premium, premium_until
                     FROM users WHERE id = ?""", (user_id,))
        user = c.fetchone()
        conn.close()
        if not user:
            return jsonify({'success': False, 'message': 'User not found'}), 404
        is_premium = bool(user[6])
        premium_until = user[7]
        if is_premium and premium_until:
            try:
                if datetime.fromisoformat(premium_until) < datetime.now():
                    is_premium = False
                    premium_until = None
            except:
                pass
        return jsonify({
            'success': True,
            'user': {
                'id': user[0],
                'email': user[1],
                'name': user[2],
                'username': user[3],
                'avatar': user[4],
                'isAdmin': bool(user[5]),
                'isPremium': is_premium,
                'premiumUntil': premium_until
            }
        })
    except Exception as e:
        print(f"Get current user error: {e}")
        return jsonify({'success': False, 'message': 'Server error'}), 500

# ============ ЗАПУСК ============

if __name__ == '__main__':
    print("=" * 60)
    print("🎵 EIMOR MUSIC - Музыкальная платформа")
    print("=" * 60)
    print("\n🔧 Инициализация базы данных...")
    init_db()
    print("✅ База данных готова!")
    
    print("\n👤 Администратор:")
    print(f"   📧 Email: {os.environ.get('ADMIN_EMAIL', 'eimorsmusic@gmail.com')}")
    print(f"   🔑 Пароль: {os.environ.get('ADMIN_PASSWORD', 'admin123')}")
    
    print("\n💬 Поддержка: https://t.me/timelyx_suport")
    
    is_production = os.environ.get('FLASK_ENV') == 'production'
    
    if is_production:
        print("\n🚀 Режим: ПРОДАКШН")
        print("⚠️  DEBUG режим отключен")
        app.run(debug=False, host='0.0.0.0', port=5000)
    else:
        print("\n🛠️  Режим: РАЗРАБОТКА")
        print("⚠️  Для продакшена установите FLASK_ENV=production")
        print(f"\n🌐 Сервер запущен: http://localhost:5000")
        print("=" * 60)
        app.run(debug=True, host='0.0.0.0', port=5000)
