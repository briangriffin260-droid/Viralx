# ViralX Backend

## Environment Variables Required

Set these in Railway's Variables tab:

```
MONGO_URL=your_mongodb_connection_string
DB_NAME=viralx
STRIPE_API_KEY=your_stripe_key
JWT_SECRET=your_secret_key
```

## Start Command

```
uvicorn server:app --host 0.0.0.0 --port $PORT
```
