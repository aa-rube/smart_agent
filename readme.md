SMART AGENT PROD 

sudo systemctl stop smartagent

cd
cd smart_agent
git pull
sudo systemctl restart smartagent
sudo systemctl restart smartexecutor
sudo systemctl restart membership.service
sudo journalctl -u smartagent -f

cd
cd smart_agent
git pull
source ~/venv_smart_agent/bin/activate
pytest tests/



# Запустить конкретный файл тестов
pytest tests/test_payment_webhook.py

# Запустить с подробным выводом
pytest tests/ -v

# Запустить с покрытием кода
pytest tests/ --cov=bot/handlers/payment_handler --cov=bot/utils/billing_db --cov=bot/utils/notification
```




journalctl -u smartagent -f
journalctl -u smartexecutor.service -f
journalctl -u membership.service -f



# Установить зависимости для тестирования
pip install pytest pytest-asyncio pytest-mock



curl -sS -X POST "http://127.0.0.1:6000/members/invite" \
  -H "Content-Type: application/json" \
  -d '{"user_id": 6714443394}'

  curl -sS -X POST "http://127.0.0.1:6000/members/remove" \
  -H "Content-Type: application/json" \
  -d '{"user_id": 6714443394}'


sudo systemctl stop smartagent
sudo systemctl stop smartexecutor
sudo systemctl stop membership.service


Список подписок за 1р и на полную стоимость

WITH first_subscriptions AS (
  SELECT
    pl.user_id,
    MIN(pl.created_at) AS trial_started_at,  -- оставляем имя поля как в исходнике
    JSON_UNQUOTE(JSON_EXTRACT(pl.metadata_json, '$.plan_code')) AS plan_code
  FROM payment_log AS pl
  WHERE pl.status = 'succeeded'
    AND (pl.event = 'payment.succeeded' OR pl.event IS NULL OR pl.event = '')

    -- 1) триал 1 ₽ (включая fallback 'trial_tokenless')
    AND (
      (
        JSON_UNQUOTE(JSON_EXTRACT(pl.metadata_json, '$.phase')) IN ('trial','trial_tokenless')
        AND pl.amount_value = '1.00'
      )
      OR
      -- 2) первая полноценная оплата без триала (phase отсутствует/пустой и сумма НЕ 1 ₽)
      (
        (JSON_UNQUOTE(JSON_EXTRACT(pl.metadata_json, '$.phase')) IS NULL
         OR JSON_UNQUOTE(JSON_EXTRACT(pl.metadata_json, '$.phase')) = '')
        AND pl.amount_value <> '1.00'
      )
    )
  GROUP BY
    pl.user_id,
    JSON_UNQUOTE(JSON_EXTRACT(pl.metadata_json, '$.plan_code'))
)
SELECT
  ROW_NUMBER() OVER (ORDER BY fs.trial_started_at) AS n,
  fs.user_id,
  u.username,
  fs.trial_started_at,
  fs.plan_code
FROM first_subscriptions fs
LEFT JOIN users u ON u.user_id = fs.user_id
ORDER BY fs.trial_started_at;




journalctl -u smartexecutor -f
sudo systemctl restart executor

sudo systemctl daemon-reload

sudo systemctl start smartagent
sudo systemctl stop smartagent
sudo systemctl restart smartagent
sudo systemctl enable smartagent
sudo systemctl disable smartagent
sudo systemctl status smartagent
journalctl -u smartagent -f


# /etc/systemd/system/smartagent.service
[Unit]
Description=Smart Agent Telegram Bot
After=network.target

[Service]
WorkingDirectory=/home/smartagent/smart_agent
ExecStart=/home/smartagent/smart_agent/.venv/bin/python -m bot.run
User=smart
Group=smart
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target


#########################

sudo systemctl start smartexecutor
sudo systemctl stop smartexecutor
sudo systemctl restart smartexecutor
sudo systemctl enable smartexecutor
sudo systemctl disable smartexecutor
sudo systemctl status smartexecutor
journalctl -u smartexecutor -f

# /etc/systemd/system/smartexecutor.service
[Unit]
Description=Smart Agent Executor Webhook
After=network.target

[Service]
WorkingDirectory=/home/smartagent/smart_agent
ExecStart=/home/smartagent/smart_agent/.venv/bin/python -m executor.app
User=smart
Group=smart
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target





















NONONNONONOON

cd
cd test/smart_agent
git pull
sudo systemctl restart smartagent_test
sudo systemctl restart smartexecutor_test
sudo journalctl -u smartagent_test -f

sudo systemctl stop smartagent_test
sudo systemctl stop smartexecutor_test

sudo systemctl disable smartagent_test
sudo systemctl disable smartexecutor_test

cd
cd test/smart_agent
git pull
sudo systemctl restart smartagent_test
sudo systemctl restart smartexecutor_test
sudo journalctl -u smartexecutor_test -f


sudo systemctl stop smartagent_test


sudo systemctl daemon-reload

sudo systemctl enable smartagent_test
sudo systemctl enable smartexecutor_test

sudo systemctl disable smartagent_test
sudo systemctl disable smartexecutor_test

sudo systemctl stop smartagent_test
sudo systemctl stop smartexecutor_test



sudo nano /etc/systemd/system/smartagent_test.service
[Unit]
Description=Smart Agent Telegram Bot
After=network.target

[Service]
WorkingDirectory=/home/smart/test/smart_agent
ExecStart=/home/smart/smart_agent/venv/bin/python -m bot.run
User=smart
Group=smart
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target


#########################


sudo nano /etc/systemd/system/smartexecutor_test.service

[Unit]
Description=SmartExecutor App
After=network.target

[Service]
User=smart
Group=smart
WorkingDirectory=/home/smart/test/smart_agent
Environment="PATH=/home/smart/smart_agent/venv/bin"
ExecStart=/home/smart/smart_agent/venv/bin/python -m executor.app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target



SELECT * FROM charge_attempts;
SELECT * FROM description_history;
SELECT * FROM payment_log;
SELECT * FROM payment_methods;
SELECT * FROM review_history;
SELECT * FROM subscriptions;
SELECT * FROM summary_history;
SELECT * FROM trials;
SELECT * FROM user_consents;
SELECT * FROM users;
