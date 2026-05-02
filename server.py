from flask import Flask, Response, request
import requests
import os

app = Flask(__name__)

BOT_TOKEN = "8620168512:AAEtqbj_2lL5_eKfHTjM_BmZC4HihidStVg"

@app.route('/')
def index():
    return 'KinoUz Video Server ishlayapti!'

@app.route('/video/<file_id>')
def stream_video(file_id):
    try:
        # Telegram dan file URL olish
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}")
        data = r.json()
        if not data.get('ok'):
            return 'Fayl topilmadi', 404
        
        file_path = data['result']['file_path']
        video_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        
        # Range header ni qo'llab-quvvatlash (video player uchun kerak)
        range_header = request.headers.get('Range', None)
        headers = {}
        if range_header:
            headers['Range'] = range_header
        
        # Telegram dan video ni stream qilish
        video_response = requests.get(video_url, headers=headers, stream=True)
        
        def generate():
            for chunk in video_response.iter_content(chunk_size=8192):
                yield chunk
        
        response_headers = {
            'Content-Type': 'video/mp4',
            'Accept-Ranges': 'bytes',
            'Access-Control-Allow-Origin': '*',
        }
        
        if 'Content-Range' in video_response.headers:
            response_headers['Content-Range'] = video_response.headers['Content-Range']
        if 'Content-Length' in video_response.headers:
            response_headers['Content-Length'] = video_response.headers['Content-Length']
        
        status_code = 206 if range_header else 200
        return Response(generate(), status=status_code, headers=response_headers)
    
    except Exception as e:
        return f'Xato: {str(e)}', 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
