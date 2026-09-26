import argparse
import atexit
import fcntl
from pathlib import Path
from .app import create_app
from .douyin import DemoAdapter


def main():
    parser = argparse.ArgumentParser(description='抖音用户与消息管理控制台（本机运行）')
    parser.add_argument('--demo', action='store_true', help='模拟账号和数据，不发出真实抖音请求')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    data_dir = args.data_dir or Path('datas/web-demo' if args.demo else 'datas/web')
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    instance_lock = (data_dir / 'server.lock').open('w')
    try:
        fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('这个数据目录已有服务运行，不能重复启动执行器')
    app = create_app(data_dir, adapter=DemoAdapter() if args.demo else None)
    atexit.register(app.extensions['console'].close)
    mode = '演示模式：所有账号、搜索和发送均为模拟' if args.demo else '真实模式：仅用户创建并开始任务后才发送'
    print(f'\n{mode}\n打开 http://127.0.0.1:{args.port}\n', flush=True)
    app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
