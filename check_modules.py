import requests
import subprocess
import time

log = open('web_check.log', 'w')
err = open('web_check_err.log', 'w')
process = subprocess.Popen(['python', '-m', 'sweave.cli.main', 'web', '--host', '127.0.0.1', '--port', '9095'], stdout=log, stderr=err)

for i in range(30):
    time.sleep(1)
    try:
        r = requests.get('http://127.0.0.1:9095/api/agents', timeout=2)
        if r.status_code == 200:
            break
    except:
        pass

try:
    # Check that all JS files are accessible and have correct content
    base = 'http://127.0.0.1:9095/static/js'
    for js_file in ['app.js', 'api.js', 'theme.js', 'websocket.js', 'modal.js', 'notification.js']:
        r = requests.get(f'{base}/{js_file}')
        has_export = 'export' in r.text
        has_class = 'class' in r.text
        print(f'{js_file}: status={r.status_code}, size={len(r.text)}, has_export={has_export}')

    # Check app.js
    r = requests.get(f'{base}/app.js')
    content = r.text
    if 'DOMContentLoaded' in content:
        print('DOMContentLoaded listener: OK')
    if 'elements.app.classList.remove' in content:
        print('Remove hidden class: OK')
    if 'classList.remove(\'hidden\')' in content:
        print('Remove hidden class (alt syntax): OK')

    # Check the first 200 chars
    print('First 200 chars of app.js:')
    print(content[:200])

finally:
    process.terminate()
    try: process.wait(timeout=5)
    except: process.kill()
    log.close()
    err.close()