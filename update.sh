#!/bin/bash
cd ~/ubotcode || exit
git pull origin master
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart fastapi.service  # Это если вы используете systemd для запуска бота
