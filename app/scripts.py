import uvicorn


def dev():
    uvicorn.run("app.main:app", reload=True, host="0.0.0.0", port=8000)


def start():
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
