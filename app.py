from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import sqlite3
import os
import threading
import time
from datetime import datetime
import re

app = Flask(__name__)

ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = 'whatsapp:+14155238886'

def get_db():
    conn = sqlite3.connect('/data/reminders.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs('/data', exist_ok=True)
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            reminder_type TEXT NOT NULL,
            reminder_time DATETIME NOT NULL,
            done INTEGER DEFAULT 0,
            snooze_count INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

TYPES = {
    '1': '📱 סים מגיע לתאריך',
    '2': '🌙 לחזור ללקוח בערב',
    '3': '⏰ לחזור ללקוח מאוחר יותר',
    '4': '❓ שאלה שנדחתה (מוקד סגור)',
    '5': '🔧 שירות שביקשו',
}

sessions = {}

def parse_datetime(text):
    patterns = [
        r'(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\s+(\d{1,2}):(\d{2})',
        r'(\d{1,2})[/.](\d{1,2})\s+(\d{1,2}):(\d{2})',
    ]
    now = datetime.now()
    for p in patterns:
        m = re.search(p, text)
        if m:
            groups = m.groups()
            if len(groups) == 5:
                day, month, year, hour, minute = groups
                year = int(year)
                if year < 100:
                    year += 2000
            else:
                day, month, hour, minute = groups
                year = now.year
            try:
                dt = datetime(int(year), int(month), int(day), int(hour), int(minute))
                return dt
            except:
                pass
    return None

def send_whatsapp(to, message):
    client = Client(ACCOUNT_SID, AUTH_TOKEN)
    client.messages.create(
        from_=TWILIO_WHATSAPP_NUMBER,
        to=f'whatsapp:{to}',
        body=message
    )

def reminder_checker():
    while True:
        try:
            conn = get_db()
            now = datetime.now().strftime('%Y-%m-%d %H:%M')
            reminders = conn.execute(
                "SELECT * FROM reminders WHERE done=0 AND datetime(reminder_time) <= datetime(?)",
                (now,)
            ).fetchall()
            for r in reminders:
                snooze = r['snooze_count']
                msg = f"תזכורת\n"
                msg += f"לקוח: {r['customer_name']}\n"
                msg += f"סוג: {r['reminder_type']}\n\n"
                msg += f"השב סגור {r['id']} לסיום\n"
                msg += f"השב נודניק {r['id']} לתזכורת בעוד 30 דקות"
                send_whatsapp(r['phone'], msg)
                conn.execute("UPDATE reminders SET done=1 WHERE id=?", (r['id'],))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Checker error: {e}")
        time.sleep(60)

@app.route('/webhook', methods=['POST'])
def webhook():
    incoming = request.form.get('Body', '').strip()
    phone = request.form.get('From', '').replace('whatsapp:', '')
    resp = MessagingResponse()
    msg = resp.message()
    if incoming.startswith('סגור '):
        rid = incoming.split(' ')[1]
        conn = get_db()
        conn.execute("UPDATE reminders SET done=1 WHERE id=? AND phone=?", (rid, phone))
        conn.commit()
        conn.close()
        msg.body(f"תזכורת #{rid} סומנה כטופלה!")
        return str(resp)
    if incoming.startswith('נודניק '):
        rid = incoming.split(' ')[1]
        conn = get_db()
        r = conn.execute("SELECT * FROM reminders WHERE id=? AND phone=?", (rid, phone)).fetchone()
        if r:
            from datetime import timedelta
            new_time = datetime.now() + timedelta(minutes=30)
            conn.execute(
                "UPDATE reminders SET done=0, reminder_time=?, snooze_count=snooze_count+1 WHERE id=?",
                (new_time.strftime('%Y-%m-%d %H:%M'), rid)
            )
            conn.commit()
        conn.close()
        msg.body("אזכיר אותך שוב בעוד 30 דקות!")
        return str(resp)
    if incoming in ['רשימה', 'list']:
        conn = get_db()
        reminders = conn.execute(
            "SELECT * FROM reminders WHERE phone=? AND done=0 ORDER BY reminder_time",
            (phone,)
        ).fetchall()
        conn.close()
        if not reminders:
            msg.body("אין לך תזכורות פתוחות")
        else:
            text = "התזכורות הפתוחות שלך:\n\n"
            for r in reminders:
                dt = datetime.strptime(r['reminder_time'], '%Y-%m-%d %H:%M')
                text += f"#{r['id']} {r['reminder_type']}\n"
                text += f"לקוח: {r['customer_name']}\n"
                text += f"מתי: {dt.strftime('%d/%m %H:%M')}\n\n"
            msg.body(text)
        return str(resp)
    session = sessions.get(phone, {})
    if not session:
        text = "שלום! בחר סוג תזכורת:\n\n"
        for k, v in TYPES.items():
            text += f"{k}. {v}\n"
        text += "\nאו שלח רשימה לראות תזכורות פתוחות"
        sessions[phone] = {'step': 'type'}
        msg.body(text)
        return str(resp)
    step = session.get('step')
    if step == 'type':
        if incoming in TYPES:
            sessions[phone]['type'] = TYPES[incoming]
            sessions[phone]['step'] = 'name'
            msg.body("מה שם הלקוח?")
        else:
            msg.body("שלח מספר בין 1-5 בבקשה")
        return str(resp)
    if step == 'name':
        sessions[phone]['name'] = incoming
        sessions[phone]['step'] = 'time'
        msg.body("מתי לתזכר? שלח תאריך ושעה\nלדוגמה: 15/06 18:00")
        return str(resp)
    if step == 'time':
        dt = parse_datetime(incoming)
        if dt:
            conn = get_db()
            conn.execute(
                "INSERT INTO reminders (phone, customer_name, reminder_type, reminder_time) VALUES (?,?,?,?)",
                (phone, session['name'], session['type'], dt.strftime('%Y-%m-%d %H:%M'))
            )
            conn.commit()
            conn.close()
            del sessions[phone]
            msg.body(f"נשמר!\n\n{session['type']}\nלקוח: {session['name']}\nמתי: {dt.strftime('%d/%m/%Y %H:%M')}\n\nאקרא לך בדיוק בזמן!")
        else:
            msg.body("לא הבנתי את התאריך. נסה שוב:\nלדוגמה: 15/06 18:00")
        return str(resp)
    del sessions[phone]
    msg.body("שלח כל הודעה להתחיל תזכורת חדשה")
    return str(resp)

if _name_ == '_main_':
    init_db()
    checker_thread = threading.Thread(target=reminder_checker, daemon=True)
    checker_thread.start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
