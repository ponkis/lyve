module.exports = {
  apps: [{
    name: "lyve",
    script: "./venv/bin/gunicorn",
    args: "--workers 1 --threads 4 --bind 127.0.0.1:5500 wsgi:app",
    cwd: "/home/ponkis/lyve",
    interpreter: "none",
    env: {
      FLASK_ENV: "production",
      PYTHONPATH: "."
    }
  }]
}
