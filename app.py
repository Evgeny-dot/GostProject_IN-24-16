import os
import pandas as pd
import numpy as np
from scipy import stats
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, jsonify, send_file, session
import plotly.graph_objects as go
import json
import uuid
from markupsafe import Markup
from werkzeug.utils import secure_filename
from datetime import datetime
from io import BytesIO
import xlsxwriter
import plotly.io as pio
from functools import wraps

# Инициализация Flask приложения
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'xlsx', 'xls'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
app.secret_key = 'your-secret-key-here'  # Замените на реальный секретный ключ
app.config['API_KEYS'] = {'test_key': 'test_secret'}  # Простая система API ключей

def allowed_file(filename):
    """Проверяет, что расширение файла находится в списке разрешенных"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Создание папки для загрузок
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


def api_key_required(f):
    """Декоратор для проверки API ключа"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'X-API-KEY' not in request.headers:
            return jsonify({'error': 'API key is missing'}), 401
        api_key = request.headers['X-API-KEY']
        if api_key not in app.config['API_KEYS']:
            return jsonify({'error': 'Invalid API key'}), 403
        return f(*args, **kwargs)

    return decorated_function


def process_data(df, calculations, reference_value=None):
    """Обработка данных и расчет показателей (используется и в API, и в веб-интерфейсе)"""
    results = {}

    if 'repeatability' in calculations and 'Data' in df.columns:
        results['Повторяемость'] = calculate_repeatability(df['Data'].values)

    if 'reproducibility' in calculations and 'Laboratory' in df.columns:
        lab_data = [group['Data'].values for _, group in df.groupby('Laboratory')]
        results['Воспроизводимость'] = calculate_reproducibility(lab_data)

    if 'trueness' in calculations and reference_value is not None and 'Data' in df.columns:
        results['Правильность'] = calculate_trueness(df['Data'].values, reference_value)

    graphs = generate_gost_graphs(df, reference_value)

    return results, graphs


def calculate_repeatability(data):
    """Расчет показателей повторяемости"""
    if len(data) < 2:
        return {'Ошибка': 'Недостаточно данных для расчета повторяемости (нужно минимум 2 измерения)'}

    s_r = np.std(data, ddof=1)
    r = 2.8 * s_r
    return {
        'Стандартное отклонение повторяемости (s_r)': float(s_r),
        'Предел повторяемости (r)': float(r),
        'Описание': 'Повторяемость характеризует степень близости результатов измерений, полученных в одинаковых условиях'
    }


def calculate_reproducibility(data):
    """Расчет показателей воспроизводимости"""
    if len(data) < 2:
        return {'Ошибка': 'Недостаточно лабораторий для расчета воспроизводимости (нужно минимум 2 лаборатории)'}

    s_r = np.mean([np.std(lab_data, ddof=1) for lab_data in data if len(lab_data) > 1])
    s_L = np.std([np.mean(lab_data) for lab_data in data], ddof=1)
    s_R = np.sqrt(s_r ** 2 + s_L ** 2)
    R = 2.8 * s_R
    return {
        'Среднее стандартное отклонение повторяемости (s_r)': float(s_r),
        'Стандартное отклонение воспроизводимости (s_L)': float(s_L),
        'Стандартное отклонение промежуточной прецизионности (s_R)': float(s_R),
        'Предел воспроизводимости (R)': float(R),
        'Описание': 'Воспроизводимость характеризует степень близости результатов измерений, полученных в разных условиях (разные лаборатории)'
    }


def calculate_trueness(data, reference_value):
    """Расчет показателей правильности"""
    if len(data) < 2:
        return {'Ошибка': 'Недостаточно данных для оценки правильности (нужно минимум 2 измерения)'}

    mean = np.mean(data)
    delta = mean - reference_value
    n = len(data)
    s_r = np.std(data, ddof=1)
    t_value = abs(delta) / (s_r / np.sqrt(n))
    t_critical = stats.t.ppf(0.975, n - 1)
    significant = t_value > t_critical
    return {
        'Среднее значение': float(mean),
        'Опорное значение': float(reference_value),
        'Смещение (Δ)': float(delta),
        't-критерий': float(t_value),
        'Критическое значение t-критерия (α=0.05)': float(t_critical),
        'Статистическая значимость смещения': 'Да' if significant else 'Нет',
        'Описание': 'Правильность характеризует степень близости среднего результата к истинному (опорному) значению'
    }


def generate_gost_graphs(df, reference_value=None):
    """Генерация графиков согласно ГОСТ"""
    graphs = {}

    # Создаем фигуры Plotly
    if 'Data' in df.columns:
        # Гистограмма
        fig = go.Figure(data=[go.Histogram(x=df['Data'], nbinsx=20)])
        fig.update_layout(title='Гистограмма распределения')
        graphs['histogram'] = fig.to_html(full_html=False)

        # Контрольная карта
        if 'Date' in df.columns or 'Order' in df.columns:
            fig = go.Figure()
            x_col = 'Date' if 'Date' in df.columns else 'Order'
            df_sorted = df.sort_values(x_col)
            fig.add_trace(go.Scatter(x=df_sorted[x_col], y=df_sorted['Data']))
            graphs['control_chart'] = fig.to_html(full_html=False)

    if 'Laboratory' in df.columns:
        # Боксплот
        fig = go.Figure()
        for lab, group in df.groupby('Laboratory'):
            fig.add_trace(go.Box(y=group['Data'], name=lab))
        graphs['boxplot'] = fig.to_html(full_html=False)

    return graphs


def save_results_to_excel(results, graphs, df, reference_value=None):
    """Сохранение результатов в Excel"""
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output)

    # Лист с результатами
    ws_results = workbook.add_worksheet('Результаты')
    bold = workbook.add_format({'bold': True})

    # Записываем результаты
    row = 0
    for analysis_name, analysis_data in results.items():
        ws_results.write(row, 0, analysis_name, bold)
        row += 1
        for param, value in analysis_data.items():
            ws_results.write(row, 0, param)
            ws_results.write(row, 1, value)
            row += 1
        row += 1

    # Лист с данными
    if df is not None:
        ws_data = workbook.add_worksheet('Данные')
        # Записываем заголовки
        ws_data.write_row(0, 0, df.columns, bold)
        # Записываем данные
        for i, row_data in enumerate(df.values, 1):
            ws_data.write_row(i, 0, row_data)

    workbook.close()
    output.seek(0)
    return output


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/preview', methods=['POST'])
def preview_data():
    if 'file' not in request.files:
        return jsonify({'error': 'Файл не был загружен'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Не выбран файл для загрузки'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Недопустимый тип файла'}), 400

    try:
        # Сохраняем файл временно
        filename = secure_filename(f"preview_{uuid.uuid4().hex}.xlsx")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Читаем данные
        df = pd.read_excel(filepath)

        # Удаляем временный файл
        os.remove(filepath)

        # Возвращаем предпросмотр данных
        preview_data = df.head(10).to_dict('records')
        columns = list(df.columns)

        return jsonify({
            'success': True,
            'preview': preview_data,
            'columns': columns,
            'row_count': len(df)
        })

    except Exception as e:
        return jsonify({'error': f"Ошибка обработки: {str(e)}"}), 500


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return render_template('error.html', message="Файл не был загружен")

    file = request.files['file']
    if file.filename == '':
        return render_template('error.html', message="Не выбран файл для загрузки")

    if not allowed_file(file.filename):
        return render_template('error.html', message="Недопустимый тип файла")

    try:
        filename = secure_filename(f"{uuid.uuid4().hex}.xlsx")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        session['last_file'] = filepath

        calculations = request.form.getlist('calculations')
        reference_value = float(request.form['reference_value']) if 'trueness' in calculations and request.form.get(
            'reference_value') else None

        # Обработка файла
        df = pd.read_excel(filepath)
        results, graphs = process_data(df, calculations, reference_value)

        return render_template(
            'results.html',
            results=results,
            graphs=graphs,
            df=df.to_dict('records'),
            reference_value=reference_value,
            now=datetime.now()
        )

    except Exception as e:
        return render_template('error.html', message=f"Ошибка обработки: {str(e)}")


@app.route('/download_results', methods=['POST'])
def download_results():
    try:
        data = request.get_json()
        filepath = session.get('last_file')

        if not filepath or not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404

        df = pd.read_excel(filepath)
        results = data.get('results', {})
        reference_value = data.get('reference_value')

        excel_file = save_results_to_excel(
            results,
            {},  # Графики не экспортируем в этой версии
            df,
            reference_value
        )

        return send_file(
            excel_file,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='analysis_results.xlsx'
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/gost')
def gost():
    return render_template('gost.html')


# API Endpoints
@app.route('/api/v1/calculate', methods=['POST'])
@api_key_required
def api_calculate():
    """API endpoint для расчета показателей"""
    try:
        if not request.is_json:
            return jsonify({'error': 'Request must be JSON'}), 400

        data = request.get_json()

        # Проверка обязательных полей
        if 'data' not in data and 'file' not in data:
            return jsonify({'error': 'Either "data" or "file" must be provided'}), 400

        calculations = data.get('calculations', ['repeatability', 'reproducibility', 'trueness'])
        reference_value = data.get('reference_value')

        # Обработка данных
        if 'file' in data:
            # Обработка файла в base64 (можно расширить)
            return jsonify({'error': 'File upload via API not implemented yet'}), 501
        else:
            # Обработка JSON данных
            df = pd.DataFrame(data['data'])
            results, graphs = process_data(df, calculations, reference_value)

        return jsonify({
            'success': True,
            'results': results,
            'graphs': {k: v for k, v in graphs.items() if k in ['histogram', 'boxplot']}
            # Фильтруем только ключевые графики
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/docs', methods=['GET'])
def api_docs():
    """Документация API"""
    docs = {
        'endpoints': {
            '/api/v1/calculate': {
                'method': 'POST',
                'description': 'Calculate measurement accuracy metrics',
                'parameters': {
                    'data': 'Array of measurement data (required if no file)',
                    'file': 'Base64 encoded file (optional)',
                    'calculations': 'Array of calculations to perform (repeatability, reproducibility, trueness)',
                    'reference_value': 'Reference value for trueness calculation'
                },
                'example_request': {
                    'data': [
                        {'Laboratory': 'Lab1', 'Data': 10.1},
                        {'Laboratory': 'Lab1', 'Data': 10.2},
                        {'Laboratory': 'Lab2', 'Data': 10.3}
                    ],
                    'calculations': ['repeatability', 'reproducibility'],
                    'reference_value': 10.0
                }
            }
        },
        'authentication': {
            'header': 'X-API-KEY',
            'note': 'Contact administrator to get API key'
        }
    }
    return jsonify(docs)


@app.route('/api/docs')
def api_documentation():
    return render_template('api_docs.html')
if __name__ == '__main__':
    app.run(debug=True, port=5001)