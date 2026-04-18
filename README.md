# МемСайт — FastAPI + PostgreSQL + Docker

## Структура проекта

Fast-api-master/
├── main.py              # FastAPI приложение (маршруты, логика)
├── database.py          # SQLAlchemy модели и подключение к БД
├── requirements.txt     # Python-зависимости
├── Dockerfile           # Образ для FastAPI
├── docker-compose.yml   # Оркестрация: app + PostgreSQL
├── .env                 # Переменные окружения
├── .dockerignore
├── static/              # Картинки-мемы
└── templates/
    └── index.html       # Фронтенд (Jinja2)
## Быстрый старт (Windows)

### Требования
- [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) (включает Docker Compose)

### Запуск

Bash

# 1. Открыть папку проекта в терминале (PowerShell / CMD)
cd Fast-api-master

# 2. Собрать и запустить контейнеры
docker compose up --build

# Сайт доступен по адресу:
# http://localhost:8000
### Остановка

Bash

docker compose down
### Полный сброс (включая данные БД)

Bash

docker compose down -v
## API эндпоинты

| Метод | URL | Описание |
|-------|-----|----------|
| GET | / | Главная страница |
| POST | /api/register | Регистрация |
| POST | /api/login | Вход |
| POST | /api/logout | Выход |
| GET | /api/me | Текущий пользователь |
| GET | /api/meme | Случайный мем (только авторизованным) |
| GET | /api/users | Список пользователей (только авторизованным) |

## База данных

### Таблицы

users
| Колонка | Тип | Описание |
|---------|-----|----------|
| id | integer | PK |
| username | varchar(50) | Уникальный логин |
| password_hash | varchar(255) | bcrypt-хеш пароля |
| created_at | timestamp | Дата регистрации |

sessions
| Колонка | Тип | Описание |
|---------|-----|----------|
| id | integer | PK |
| session_id | varchar(36) | UUID сессии (cookie) |
| username | varchar(50) | Логин пользователя |
| created_at | timestamp | Дата создания |

### Подключение к БД вручную (psql)

Bash

docker exec -it fastapi_db psql -U fastapi_user -d fastapi_db
## Изменения относительно оригинала

- users.json → PostgreSQL (данные сохраняются между перезапусками)
- Пароли хранятся в виде bcrypt-хешей (не открытым текстом)
- Сессии хранятся в БД (не в памяти — не теряются при перезапуске)
- Добавлен эндпоинт /api/users для просмотра пользователей
