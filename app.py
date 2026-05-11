import os
import secrets
from datetime import datetime, timedelta, timezone, date as date_cls

def utcnow():
    return datetime.now(timezone.utc)

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user,
                         login_required, logout_user, current_user)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

_db_url = os.environ.get('DATABASE_URL', 'sqlite:///todo.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)

app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY') or secrets.token_hex(32),
    SQLALCHEMY_DATABASE_URI=_db_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please sign in to continue.'
login_manager.login_message_category = 'warning'


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    todos = db.relationship('TodoItem', backref='user', lazy=True,
                            cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class TodoItem(db.Model):
    __tablename__ = 'todos'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    completed = db.Column(db.Boolean, default=False, nullable=False)
    priority = db.Column(db.String(10), default='medium', nullable=False)
    due_date = db.Column(db.Date, nullable=True)
    date_created = db.Column(db.DateTime, default=utcnow)
    date_updated = db.Column(db.DateTime, default=utcnow,
                             onupdate=utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.context_processor
def inject_globals():
    return {'now': utcnow()}


# ── Public ──────────────────────────────────────────────────────────────────

@app.route('/')
def landing():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return redirect(url_for('login'))


@app.route('/welcome')
def welcome():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('landing.html')


# ── App (authenticated) ──────────────────────────────────────────────────────

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        priority = request.form.get('priority', 'medium')

        due_date_str = request.form.get('due_date', '').strip()
        due_date = None
        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        if not title:
            flash('Task title cannot be empty.', 'danger')
            return redirect(url_for('index'))
        if len(title) > 200:
            flash('Title must be under 200 characters.', 'danger')
            return redirect(url_for('index'))
        if priority not in ('low', 'medium', 'high'):
            priority = 'medium'

        db.session.add(TodoItem(
            title=title, description=description,
            priority=priority, due_date=due_date,
            user_id=current_user.id
        ))
        db.session.commit()
        flash('Task created.', 'success')
        return redirect(url_for('index'))

    search = request.args.get('q', '').strip()
    status_filter = request.args.get('s', 'all')

    query = TodoItem.query.filter_by(user_id=current_user.id)
    if search:
        query = query.filter(db.or_(
            TodoItem.title.ilike(f'%{search}%'),
            TodoItem.description.ilike(f'%{search}%'),
        ))
    today = utcnow().date()

    if status_filter == 'active':
        query = query.filter_by(completed=False)
    elif status_filter == 'done':
        query = query.filter_by(completed=True)
    elif status_filter == 'overdue':
        query = query.filter(
            TodoItem.completed == False,
            TodoItem.due_date < today,
            TodoItem.due_date.isnot(None)
        )

    todos = query.order_by(
        TodoItem.completed.asc(), TodoItem.date_created.desc()
    ).all()

    base = TodoItem.query.filter_by(user_id=current_user.id)
    total = base.count()
    done = base.filter_by(completed=True).count()

    due_today = (TodoItem.query
                 .filter_by(user_id=current_user.id, completed=False)
                 .filter(TodoItem.due_date == today)
                 .all())
    overdue_count = (TodoItem.query
                     .filter_by(user_id=current_user.id, completed=False)
                     .filter(TodoItem.due_date < today,
                             TodoItem.due_date.isnot(None))
                     .count())

    return render_template('dashboard.html',
                           todos=todos, total=total,
                           done=done, active=total - done,
                           search=search, sf=status_filter,
                           due_today=due_today,
                           overdue_count=overdue_count,
                           today=today)


@app.route('/toggle/<int:tid>')
@login_required
def toggle(tid):
    todo = db.get_or_404(TodoItem, tid)
    if todo.user_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    todo.completed = not todo.completed
    db.session.commit()
    return redirect(url_for('index'))


@app.route('/delete/<int:tid>')
@login_required
def delete(tid):
    todo = db.get_or_404(TodoItem, tid)
    if todo.user_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))
    db.session.delete(todo)
    db.session.commit()
    flash('Task deleted.', 'success')
    return redirect(url_for('index'))


@app.route('/edit/<int:tid>', methods=['GET', 'POST'])
@login_required
def edit(tid):
    todo = db.get_or_404(TodoItem, tid)
    if todo.user_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        priority = request.form.get('priority', 'medium')

        due_date_str = request.form.get('due_date', '').strip()
        due_date = None
        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        if not title:
            flash('Title cannot be empty.', 'danger')
            return redirect(url_for('edit', tid=tid))
        if priority not in ('low', 'medium', 'high'):
            priority = 'medium'

        todo.title = title
        todo.description = description
        todo.priority = priority
        todo.due_date = due_date
        todo.date_updated = utcnow()
        db.session.commit()
        flash('Task updated.', 'success')
        return redirect(url_for('index'))

    return render_template('edit.html', todo=todo)


@app.route('/clear-done', methods=['POST'])
@login_required
def clear_done():
    n = TodoItem.query.filter_by(user_id=current_user.id, completed=True).delete()
    db.session.commit()
    flash(f'{n} completed task(s) removed.', 'success')
    return redirect(url_for('index'))


@app.route('/profile')
@login_required
def profile():
    base = TodoItem.query.filter_by(user_id=current_user.id)
    total = base.count()
    done = base.filter_by(completed=True).count()
    active = total - done
    rate = int((done / total * 100)) if total else 0

    week_ago = utcnow() - timedelta(days=7)
    this_week = TodoItem.query.filter(
        TodoItem.user_id == current_user.id,
        TodoItem.date_created >= week_ago
    ).count()

    high = base.filter_by(priority='high').count()
    medium = base.filter_by(priority='medium').count()
    low = base.filter_by(priority='low').count()

    recent = (TodoItem.query
              .filter_by(user_id=current_user.id)
              .order_by(TodoItem.date_created.desc())
              .limit(5).all())

    return render_template('profile.html',
                           total=total, done=done, active=active,
                           rate=rate, this_week=this_week,
                           high=high, medium=medium, low=low,
                           recent=recent)


# ── Auth ─────────────────────────────────────────────────────────────────────

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        errors = []
        if len(username) < 3:
            errors.append('Username must be at least 3 characters.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if password != confirm:
            errors.append('Passwords do not match.')
        if not errors and User.query.filter_by(username=username).first():
            errors.append('Username already taken.')

        for e in errors:
            flash(e, 'danger')
        if errors:
            return redirect(url_for('register'))

        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash(f'Welcome, {username}!', 'success')
        return redirect(url_for('index'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            flash(f'Welcome back, {username}.', 'success')
            return redirect(next_page or url_for('index'))

        flash('Invalid username or password.', 'danger')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('landing'))


with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=False)
