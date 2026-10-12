from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/cover', methods=['GET', 'POST'])
def cover():
    message = None

    if request.method == 'POST':
        teacher = request.form.get('teacher')
        class_name = request.form.get('class_name')
        room = request.form.get('room')
        subject = request.form.get('subject')
        message = f"Cover request received for {class_name} by {teacher} in room {room} for subject {subject}."

    return render_template('cover.html', message=message)

@app.route('/signin', methods=['GET', 'POST'])
def signin():
    return render_template('signin.html')

if __name__ == '__main__':
    app.run(debug=True)