if not exist .env (
    copy .env.sample .env
)
py dev.py