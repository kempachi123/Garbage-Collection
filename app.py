import os
import random
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "super_secret_peak_garbage_collector_key"
socketio = SocketIO(app)

UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DB_FILE = "database.db"

# --- DATABASE ARCHITECTURE CONFIGURATION ---
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # Enables access to columns by name like dictionary keys
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Create Users Table (Stores permanent accounts & Civic Points Tokens)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            points INTEGER DEFAULT 0
        )
    ''')
    
    # 2. Create Incident Reports Table 
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter TEXT NOT NULL,
            description TEXT NOT NULL,
            image TEXT NOT NULL,
            govt_image TEXT,
            status TEXT DEFAULT 'Work to be Done',
            lat TEXT,
            lng TEXT,
            ai_category TEXT,
            ai_risk TEXT,
            pending_waste_weight INTEGER DEFAULT 0
        )
    ''')
    
    # 3. Create Comments Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            sender TEXT NOT NULL,
            text TEXT NOT NULL
        )
    ''')
    
    # Pre-seed default core testing accounts if they don't exist yet
    cursor.execute("INSERT OR IGNORE INTO users (username, password, role, points) VALUES ('citizen', 'user123', 'user', 0)")
    cursor.execute("INSERT OR IGNORE INTO users (username, password, role, points) VALUES ('admin', 'govt123', 'govt', 0)")
    
    conn.commit()
    conn.close()

# Initialize the permanent database storage architecture
init_db()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def simulate_ai_waste_classification():
    categories = ["High-Density Plastic", "E-Waste / Circuit Boards", "Organic Bio-Degradable", "Hazardous Chemical / Glass"]
    weights = ["Medium Risk", "High Risk", "Low Risk", "Critical Risk"]
    idx = random.randint(0, 3)
    return categories[idx], weights[idx]

# Helper to compute live dashboard counters dynamically from database entries
def get_live_metrics():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Sum up actual verified waste weights mathematically
    cursor.execute("SELECT SUM(pending_waste_weight) FROM reports WHERE status='Works Done'")
    total_waste = cursor.fetchone()[0] or 0
    
    # Count how many tickets reached total resolution completion closure
    cursor.execute("SELECT COUNT(*) FROM reports WHERE status='Works Done'")
    clean_zones = cursor.fetchone()[0] or 0
    
    conn.close()
    return {
        "total_cleared_kg": total_waste,
        "active_clean_zones": clean_zones,
        "avg_resolution_hours": max(0, clean_zones * 2)
    }


@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and user['password'] == password:
            session['username'] = username
            session['role'] = user['role']
            return redirect(url_for('govt_dashboard' if user['role'] == 'govt' else 'user_dashboard'))
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        role = request.form.get('role')
        
        conn = get_db_connection()
        try:
            conn.execute('INSERT INTO users (username, password, role, points) VALUES (?, ?, ?, 0)', (username, password, role))
            conn.commit()
            flash("Account registered successfully!", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Username already exists!", "danger")
        finally:
            conn.close()
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- CITIZEN DASHBOARD ---
@app.route('/user_dashboard', methods=['GET', 'POST'])
def user_dashboard():
    if 'username' not in session or session['role'] != 'user':
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    
    if request.method == 'POST':
        description = request.form.get('description')
        file = request.files.get('photo')
        lat = request.form.get('latitude') or "12.9716" 
        lng = request.form.get('longitude') or "77.5946"
        
        if file and allowed_file(file.filename):
            ai_cat, ai_risk = simulate_ai_waste_classification()
            
            # Temporary save file name assignment
            cursor = conn.cursor()
            cursor.execute('INSERT INTO reports (reporter, description, image, lat, lng, ai_category, ai_risk) VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (session['username'], description, 'pending', lat, lng, ai_cat, ai_risk))
            new_id = cursor.lastrowid
            
            filename = secure_filename(f"report_{new_id}_{file.filename}")
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            
            cursor.execute('UPDATE reports SET image = ? WHERE id = ?', (filename, new_id))
            conn.commit()
            
            socketio.emit('new_incident_alert', {'id': new_id, 'category': ai_cat})
            return redirect(url_for('user_dashboard'))
            
    # Fetch all records dynamically compiling child arrays from relations
    db_reports = conn.execute('SELECT * FROM reports').fetchall()
    reports_list = []
    for r in db_reports:
        report_dict = dict(r)
        db_comments = conn.execute('SELECT * FROM comments WHERE report_id = ?', (r['id'],)).fetchall()
        report_dict['comments'] = [dict(c) for c in db_comments]
        reports_list.append(report_dict)
        
    # Fetch Leaderboard metrics safely excluding the system administration profiles
    db_users = conn.execute("SELECT username, points, role FROM users WHERE role != 'govt' ORDER BY points DESC").fetchall()
    leaderboard = [(u['username'], {"points": u['points']}) for u in db_users]
    
    conn.close()
    return render_template('user_dashboard.html', reports=reports_list, leaderboard=leaderboard, metrics=get_live_metrics())

@app.route('/add_comment/<int:report_id>', methods=['POST'])
def add_comment(report_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    comment_text = request.form.get('comment_text')
    if comment_text:
        conn = get_db_connection()
        conn.execute('INSERT INTO comments (report_id, sender, text) VALUES (?, ?, ?)', (report_id, session['username'], comment_text))
        conn.commit()
        conn.close()
        socketio.emit('comment_update', {'report_id': report_id, 'sender': session['username'], 'text': comment_text})
    return redirect(url_for('govt_dashboard' if session['role'] == 'govt' else 'user_dashboard'))

@app.route('/approve_work/<int:report_id>', methods=['POST'])
def approve_work(report_id):
    if 'username' not in session or session['role'] != 'user':
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    report = conn.execute('SELECT * FROM reports WHERE id = ?', (report_id,)).fetchone()
    
    if report and report['status'] == 'Pending Approval':
        # 1. Transactionally update ticket completion state status
        conn.execute("UPDATE reports SET status = 'Works Done' WHERE id = ?", (report_id,))
        # 2. Add permanent gamification metric points token increment to citizen account record table
        conn.execute("UPDATE users SET points = points + 50 WHERE username = ?", (report['reporter'],))
        conn.commit()
        socketio.emit('status_changed', {'id': report_id, 'status': 'Works Done'})
        
    conn.close()
    return redirect(url_for('user_dashboard'))

# --- GOVT OPERATIONS ---
@app.route('/govt_dashboard')
def govt_dashboard():
    if 'username' not in session or session['role'] != 'govt':
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    db_reports = conn.execute('SELECT * FROM reports').fetchall()
    reports_list = []
    for r in db_reports:
        report_dict = dict(r)
        db_comments = conn.execute('SELECT * FROM comments WHERE report_id = ?', (r['id'],)).fetchall()
        report_dict['comments'] = [dict(c) for c in db_comments]
        reports_list.append(report_dict)
    conn.close()
    
    return render_template('govt_dashboard.html', reports=reports_list, metrics=get_live_metrics())

@app.route('/update_status/<int:report_id>', methods=['POST'])
def update_status(report_id):
    if 'username' not in session or session['role'] != 'govt':
        return redirect(url_for('login'))
    new_status = request.form.get('status')
    
    conn = get_db_connection()
    conn.execute('UPDATE reports SET status = ? WHERE id = ?', (new_status, report_id))
    conn.commit()
    conn.close()
    
    socketio.emit('status_changed', {'id': report_id, 'status': new_status})
    return redirect(url_for('govt_dashboard'))

@app.route('/govt_submit_proof/<int:report_id>', methods=['POST'])
def govt_submit_proof(report_id):
    if 'username' not in session or session['role'] != 'govt':
        return redirect(url_for('login'))
        
    file = request.files.get('govt_photo')
    waste_removed = request.form.get('waste_removed') 
    
    try:
        waste_removed = int(waste_removed)
    except (TypeError, ValueError):
        waste_removed = 0
        
    if file and allowed_file(file.filename):
        filename = secure_filename(f"resolved_{report_id}_{file.filename}")
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
        conn = get_db_connection()
        conn.execute('UPDATE reports SET govt_image = ?, status = "Pending Approval", pending_waste_weight = ? WHERE id = ?',
                     (filename, waste_removed, report_id))
        conn.commit()
        conn.close()
        
        socketio.emit('status_changed', {'id': report_id, 'status': 'Pending Approval'})
        
    return redirect(url_for('govt_dashboard'))

if __name__ == '__main__':
    socketio.run(app, debug=True)