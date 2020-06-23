import os
import sys
import html
import time
import http
import socket
import datetime
import threading
import selectors
import email.utils
from http import HTTPStatus
from io import BufferedIOBase
from time import monotonic as mtime

if hasattr(selectors, "PollSelector"):
    _ServerSelector = selectors.PollSelector
else:
    _ServerSelector = selectors.SelectSelector

__wsgi_version__ = "0.2"
__socket_version__ = "0.4"
__http_version__ = "0.6"
# __all__ = []

DEFAULT_ERROR_MESSAGE = """\
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN"
        "http://www.w3.org/TR/html4/strict.dtd">
<html>
    <head>
        <meta http-equiv="Content-Type" content="text/html;charset=utf-8">
        <title>Error response</title>
    </head>
    <body>
        <h1>Error response</h1>
        <p>Error code: %(code)d</p>
        <p>Message: %(message)s.</p>
        <p>Error code explanation: %(code)s - %(explain)s.</p>
    </body>
</html>
"""

DEFAULT_ERROR_CONTENT_TYPE = "text/html;charset=utf-8"


class _SocketWriter(BufferedIOBase):
    # 简单的 socket 的可写 BufferedIOBase 实现
    # 不在缓冲区内保存数据，避免了调用 flush 的需要
    # io.BufferedIOBase
    # 支持某种缓冲的二进制流的积累，继承自 IOBase，没有公共构造器。 TODO 疑惑，公共构造器是啥？
    
    def __init__(self, sock):
        # 在本示例中 sock 指的是 connection socket 实例化对象，也就是 request 实例化对象
        self._sock = sock

    def writable(self):
        return True

    def write(self, b):
        # 写入给定的 类 bytes 对象 b，并返回写入的字节数（总是等于 b 的长度，因为如果写入失败则引发 OSError
        # 根据具体实现的不同，这些字节可能被实际写入下层流，或是出于运行效率和冗余等考虑而暂存于缓冲区
        # 当处于费阻塞模式时，如果需要将数据写入原始流但它无法在不阻塞的情况相爱接受所有数据则将引发 BlockingIOError
        # 调用者可能会在此方法返回后释放或改变 b，因此该实现应当仅在方法调用期间访问 b

        # 发送数据给套接字，在这里应该是将 b 发送给 request socket 实例化对象
        self._sock.sendall(b)
        # memoryview() 函数返回给定参数的内存查看对象(memory view)。
        # 所谓内存查看对象，是指对支持缓冲区协议的数据进行包装，在不需要复制对象基础上允许Python代码访问。
        with memoryview(b) as view:
            return view.nbytes
    
    def fileno(self):
        return self._sock.fileno()


# -------------- WSGI Handler Class --------------
class BaseHandler:
    pass


class SimpleHandler(BaseHandler):
    pass


class ServerHandler(SimpleHandler):
    pass


# -------------- Request Handler Class --------------
class BaseRequestHandler:
    # 本实例会在每次请求传入的时候被实例化一次
    # 生成器会给实例添加 request, client_address, server 变量
    # 然后调用 handle 方法
    # 为了执行一个具体的服务，你需要去做的就是获得一个定义了 handle 方法的类

    def __init__(self, request, client_address, server):
        self.reuqest = request
        self.client_address = client_address
        self.server = server
        self.setup()
        try:
            self.handle()
        finally:
            self.finish()

    def setup(self):
        pass

    def handle(self):
        pass

    def finish(self):
        pass


class StreamRequestHandler(BaseRequestHandler):
    """
    此类设置的目的是为 Stream 类的 socket 提供一个 request handler 方案
    """

    # rfile 和 wfile 的默认缓存大小
    # 默认设置 rfile 为缓存，否则对于 large data 来说可能会很慢（每个字节调用一次 getc())
    # 默认设置 wfile 为不缓存，因为：1、我们经常在 write() 之后会跟一个 read 并且需要 flush the line
    # 2. 对于无缓存的大文件写入经常会被 stdio, envn when big reads aren't.
    rbufsize = -1
    wbufsize = 0
    timeout = None # 应用在 request socket 的 timeout
    # TODO 拓展 了解 nagle 算法
    disable_nagle_algorithm = False # 禁用 socket 的 nagle 算法，仅当 wbufsize 不为 0 时使用，避免发送的包过小

    def setup(self):
        # 如何理解 setup？调用前的准备？
        # 将 request socket 实例赋值给 connection
        self.connection = self.reuqest
        # 如果设置了 timeout
        if self.timeout is not None:
            # 将设置的 timeout 赋值给 request socket 实例
            self.connection.settimeout(self.timeout)
        # 如果设置了停止使用 nagle 算法
        if self.disable_nagle_algorithm:
            # 对 socket 实例进行设置
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, True)
        
        # 配置 rfile 为
        # socket.makefile(mode="r", buffering=None, encoding=None, errors=None, newline=None)
        # 返回套接字关联的文件对象。返回对象的具体参数取决于 makefile 的参数
        # 这些参数的解释方式与内置的 open 函数相同，其中 mode 的值仅支持 r w b
        # 前桃子必须处于阻塞模式，可以有超时，如果发生超时，文件对象的内部缓存可能会以不一致的状态皆为
        # 关闭 makefile 返回的文件对象不会关闭原始套接字，除非所有其他文件对象都已关闭而且套接字对象上调用了 socket.close()
        # 在 Windows 上，由 makefile 创建的文件类对象无法作为带文件描述符的文件对象使用，如：无法作为 subprocess.Popen() 的流参数
        self.rfile = self.connection.makefile("rb", self.rbufsize)
        if self.wbufsize == 0:
            # 
            self.wfile = _SocketWriter(self.connection)
        else:
            self.wfile = self.conneciton.makefile("wb", self.wbufsize)

    def finish(self):
        if not self.wfile.closed:
            try:
                # 释放 wfile
                # File flush 用来刷新缓冲区的，即将缓冲区中的数据立刻写入文件，同时清空缓冲区，不需要是被动的等待输出缓冲区写入
                self.wfile.flush()
            except socket.error:
                pass
        self.wfile.close()
        self.rfile.close()


class BaseHTTPRequestHandler(StreamRequestHandler):
    # Python system version
    sys_version = "Python/" + sys.version.split()[0]

    # server software version
    server_version = "BaseHTTP/" + __http_version__

    error_message_format = DEFAULT_ERROR_MESSAGE
    error_content_type = DEFAULT_ERROR_CONTENT_TYPE

    # default request version
    # 默认的请求版本。 
    # 这只影响响应，直到请求行被解析为止，所以它主要决定了当客户端发送错误的请求行时，会得到什么。
    # 大多数Web服务器默认为HTTP 0.9，即不发送状态行。
    default_request_version ="HTTP/0.9"

    def parse_request(self):
        # 解析一个请求（内部的）
        # 请求应该被存储到 raw_requestline 中
        # 结果在 command  path request_version headers 中
        # 返回 True 当运行成功， 返回 False 当运行失败
        # 失败时，全部有关的错误响应都想被返回
        self.command = None
        self.request_version = version = self.defautl_request_version
        self.close_connection = True
        # slef.raw_requestline 在运行此函数之前就被 handle 函数生成了
        requestline = str(self.raw_requestline, "iso-8859-1")
        # python str rstrip 删除 string 字符串末尾的指定字符（默认为空格）.
        requestline = requestline.rstrip("\r\n")
        self.requestline = requestline
        # python str split 通过指定分隔符对字符串进行切片，如果第二个参数 num 有指定值，则分割为 num+1 个子字符串。
        # str.split(str="", num=string.count(str))
        # str -- 分隔符，默认为所有的空字符，包括空格、换行(\n)、制表符(\t)等。
        # num -- 分割次数。默认为 -1, 即分隔所有。
        words = requestline.split()
        if len(words) == 0:
            return False
        
        if len(words) >= 3:
            version = words[-1]
            try:
                if not version.startswith('HTTP/'):
                    raise ValueError
                base_version_number = version.split('/', 1)[1]
                version_number = base_version_number.split(".")
                pass
            except:
                pass


    def handle_expect_100(self):
        pass

    def handle_one_requset(self):
        pass
    
    def handle(self):
        pass

    def send_error(self, code, message=None, explain=None):
        # 发送并记录 error 回复
        # code 一个 HTTP error code
        # message 一个简单的可选的单行原因预警
        # explain 一个详细的信息默认消息包含匹配错误响应代码的长条目
        try:
            shortmsg, longmsg = self.responses[code]
        except KeyError:
            shortmsg, longmsg= "???", "???"
        
        if message in None:
            message = shortmsg
        if explain is None:
            expalin = longmsg
        
        self.log_error(f"code {code}, message {message}")
        self.send_response(code, message)
        self.send_header("Connection", "close")

        body = None
        if (code >= 200 and code not in ()):
            # html.secape(s, quote=True)
            # 将字符串 s 中的特殊字符转换为安全的 HTML 序列
            # 如果需要在 HTML 中显示可能包含此类字符的文本，请使用此选项
            # 如果可选的标志 quote 为真值，则字符 (") 和 (') 也被转换
            content = (self.error_message_format % {
                "code": code,
                "message": html.escape(message, quote=False),
                "expalin": html.escape(expalin, quote=False)
            })
            body = content.encode("UTF-8", "replace")
            self.send_header("Content-Type", self.error_content_type)
            self.send_header("COntent-Length", str(len(body)))
        self.end_headers()

        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def send_response(self, code, message=None):
        # 添加 response header 到 header 缓存中并 log response code
        # 同时发送呆着软件版本和当前信息的 two standard 标准质量 headers
        self.log_request(code)
        self.send_response_only(code, message)
        self.send_header("Server", self.version_string())
        self.send_header("Date", self.date_time_strint())

    def send_response_only(self, code, message=None):
        # 只发送响应头
        if self.request_version != "HTTP/0.9":
            if message is None:
                if code in self.responses:
                    message = self.responses[code][0]
                else:
                    message = ""
            if not hasattr(self, "_headers_buffer"):
                self._headers_buffer = []
            self._headers_buffer.append(("%s %d %s\r\n" %
                                         (self.protocol_version, code, message)).encode(
                'latin-1', 'strict'))

    def send_header(self, keyword, value):
        # 发送一个 MIME header 到 header 缓存
        if self.request_version != "HTTP/0.9":
            # TODO 疑惑 为什么要清空
            if not hasattr(self, "_headers_buffer"):
                self._headers_buffer = []
            self._headers_buffer.append(
                 ("%s: %s\r\n" % (keyword, value)).encode('latin-1', 'strict')
            )
        
        # 判断是否需要关闭连接并留下 flag
        if keyword.lower() == "connection":
            if value.lower() == "close":
                 self.close_connection = True
            elif value.lower() == "keep-alive":
                self.close_connection = False

    def end_headers(self):
        # 发送 blank line 结束 MIME headers
        if self.request_version != "HTTP/0.9":
            self._headers_buffer.append(b"\r\n")
            self.flush_header()

    def flush_headers(self):
        if hasattr(self, "_headers_buffer"):
            # TODO 疑惑 这是在做什么？
            self.wfile.write(b"".join(self._headers_buffer))
            self._headers_buffer = []

    def log_request(self, code="-", size="-"):
        if isinstance(code, HTTPStatus):
            code = code.value
        self.long_message(f"\"{self.requestline}\" {str(code)} {str(size)}")

    def log_error(self, format, *args):
        self.log_message(format, *args)

    def log_message(self, format, *args):
        sys.stderr.write(f"{self.address_string()} - - [{self.log_date_time_string()} {format % args}\n]")

    def version_string(self):
        # 返回字符串形式的 server software version
        return self.server_version + " " + self.sys_version

    def date_time_string(self, timestamp=None):
        # 返回格式化的当前日期和时间当做一个 message header
        if timestamp is None:
            timestamp = time.time()
        return email.utils.formatdate(timestamp, usegmt=True)

    def log_data_time_string(self):
        pass

    weekdayname = ["Mon", "Tue", "Web", "Thu", "Fri", "Sat", "Sun"]

    monthname = [None, "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def address_string(self):
        pass

    protocol_version = "HTTP/1.0"

    MessageClass = http.client.HTTPMessage

    responses = {
        v: (v.phrase, v.description)
        for v in HTTPStatus.__members__.values()
    }


class WSGIRequestHandler(BaseHTTPRequestHandler):
    server_version = "WSGIServer/" + __wsgi_version__

    def get_environ(self):
        pass

    def get_stderr(self):
        pass

    def handle(self):
        # 处理一个单一的 HTTP 请求
        self.raw_requestline = self.rfile.readline(65537)
        if len(self.raw_requestline) > 65536:
            self.requestline = ""
            self.request_version = ""
            self.command = ""
            self.send_error(414)
            return
        
        # 通过判断调用 parse_request 函数来解析 request
        if not self.parse_request():
            return
        
        # 设置真正的 handler
        handler = ServerHandler(self.rfile, self.wfile, self.get_stderr(), self.get_environ())
        handler.request_handler = self
        handler.run(self.server.get_app())


# -------------- Server Class --------------
class BaseServer(object):
    # 此类的目的是什么？用来创建一个基础的 socket server 管理实例
    timeout = None

    def __init__(self, server_address, ReuqestHandlerClass):
        # 基础 Server 初始化函数 | 需要传入 server address 和 Request 处理类
        # server address 用来创建 socket 实例
        # Request 处理类用来后期处理 Request 信息
        self.server_address = server_address
        self.RequestHandlerClass = ReuqestHandlerClass
        self.socket = None # 此属性不应该在这里定义，这里只是为了记录实例中有哪些属性
        """
        threading.Event()
        实现事件对象的类。
        事件对象管理一个内部标志，调用 set() 方法可将其设置为true。
        调用 clear() 方法可将其设置为false。
        调用 wait() 方法将进入阻塞直到标志为true。
        这个标志初始时为false。
        """
        # TODO 疑惑 这两个变量是用来监测什么的？
        self.__is_shut_down = threading.Event()
        self.__shutdown_request = False

    def server_forever(self, poll_interval=0.5):
        # TODO 疑惑 forever 的本质是什么？
        self.__is_shut_down.clear()

        try:
            # TODO 疑惑 这一步的意义是什么？
            with _ServerSelector() as selector:
                selectors.register(self, selectors.EVENT_READ)

                while not self.__shutdown_request:
                    # select 方法：执行实际选择，知道被监视的文件对象准备好或超时
                    ready = selector.select(poll_interval)
                    if self.__shutdown_request:
                        break
                    if ready:
                        # 如果有文件状态改变并且 request 没有 shutdown，以非阻塞状态执行 handle request
                        self._handle_request_noblock()
                
                self.service_actions()
        finally:
            self.__shutdown_request = False
            self.__is_shut_down.set()

    def _handle_request_noblock(self):
        # 处理一个请求，不阻塞
        # 假设 selector.select() 已经返回，socket 在此函数调用之前是可读的
        # 所以在 get_request() 中不应该存在阻塞
        try:
            # 尝试通过 get_reuqest 方法来获取 request 对象和 client address
            # 在本示例中，get request 定义在 TCPServer 中
            request, client_address = self.get_request()
        except OSError:
            return

        # TODO 疑惑 这个函数的作用是什么，明明就直接返回了 True
        if self.verify_request(request, client_address):
            try:
                self.process_request(request, client_address)
            except Exception:
                self.handle_error(request, client_address)
                self.shutdown_request(request)
            except:
                self.shutdown_request(request)
                raise
        else:
            self.shutdown_request(request)

    def shutdown(self):
        # 停止 server_forever loop
        self.__shutdown_request = True
        self.__is_shut_down.wait()

    def handle_request(self):
        # 处理一个 request 可能阻塞
        # TODO 疑惑 为啥还要单独写一个函数
        timeout = self.socket.gettimeout()
        if timeout is None:
            timeout = self.timeout
        elif self.timeout is not None:
            timeout = min(timeout, self.timeout)
        if timeout is not None:
            deadline = mtime() + timeout
        
        with _ServerSelector() as selector:
            selectors.register(self, selectors.EVENT_READ)

            while True:
                ready = selector.select(timeout)
                if ready:
                    return self._handel_request_noblokc()
                else:
                    if mtime is not None:
                        timeout = deadline - mtime()
                        if timeout < 0:
                            return self.handle_timeout()

    def verify_request(self, request, client_address):
        return True
    
    def process_request(self, request, client_address):
        # 调用 finish_request 方法
        self.finish_request(request, client_address)
        self.shutdown_request(request)

    def shutdown_request(self, request):
        # 调用 shutdown 关闭 request
        # 在本示例中，shutdown_request 方法被 TSPServer 类重写
        self.close_request(request)

    def handle_error(self, request, client_address):
        print("-"*40, file=sys.stderr)
        print("Exception happened during processing of request from", client_address, file=sys.stderr)

        import traceback
        traceback.print_exc()
        print("-"*40, file=sys.stderr)

    def finish_request(self, request, client_address):
        # 通过传入的 request 处理类完成对 request 处理
        # RequestHandlerClass 是另一个分析线路（Handler 分析线路
        self.RequestHandlerClass(request, client_address, self)
    

class TCPServer(BaseServer):
    # 此类的目的是什么？ 在 BaseServer 类的基础上开创一个专门用来创建 TCP socket server 的类
    address_family = socket.AF_INET
    socket_type = socket.SOCK_STREAM
    request_queue_size = 5
    allow_reuse_address =False

    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True):
        # TODO 疑惑 为什么要设置 bind_and_activate 参数？作用是什么，难道存在不需要执行绑定和执行的情况吗？
        BaseServer.__init__(self, server_address, RequestHandlerClass)
        self.socket = socket.socket(self.address_family, self.socket_type)
        if bind_and_activate:
            try:
                self.server_bind()
                self.server_activate()
            except:
                self.server_close()
                raise
        
    def server_bind(self):
        if self.allwo_reuse_address:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        # TODO 疑惑 这里对 self.server_address 重新赋值的目的是什么？
        self.server_address = self.socket.getsockname()

    def server_activate(self):
        self.socket.listen(self.request_queue_size)

    def server_close(self):
        # 这里关闭的应该是 server 对应的 socket 实例
        self.socket.close()

    def fileno(self):
        # 通过 server 对应的 socket 获取 fileno
        return self.socket.fileno()

    def get_request(self):
        # 从 socket 中获取 request 和 client address
        # socket.accept 用来接受一个连接，
        # 此 scoket 必须绑定到一个地址上并且监听连接。
        # 返回值是一个 (conn, address) 对
        # 其中 conn 是一个 新 的套接字对象，用于在此连接上收发数据
        # address 是连接另一端的套接字所绑定的地址
        return self.socket.accept()

    def shutdown_request(self, request):
        try:
            # request 的本质其实是一个 socket 实例对象，因此此处调用的是 socket 的 shutdown
            # socket.shutdown(how)
            # 关闭一半或全部的连接
            # 如果 how 为 SHUT_RD，则后续不再允许接收
            # 如果 how 为 SHUT_WR，则后续不再允许发送
            # 如果 how 为 SHUT_RDWR，则后续的发送和接收都不允许
            request.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        self.close_request(request)
    
    def close_request(self, request):
        # request 的本质其实是一个 socket 实例对象，因此此处调用的是 socket 的 close
        # socket.close(fd)
        # 关闭一个套接字文件描述符
        # 它类似于 os.close()，但专用于套接字
        # 在某些平台上（特别是在 Windows 上），os.close() 对套接字文件描述符无效
        request.close()


class HTTPServer(TCPServer):
    # 此类的目的是什么？在 TCPServer 类的基础上创建一个专门用来创建 HTTP socket server 的类
    allow_reuse_address = 1

    def server_bind(self):
        # 重写 server_bind 方法
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = socket.getfqdn(host)
        self.server_port = port


class WSGIServer(HTTPServer):
    """此类的目的是什么？在 HTTPServer 的基础上创建符合 WSGI 规范的 WSGI socket server 的类
    class variables: 
        timeout = None
        address_family = socket.AF_INET
        socket_type = socket.SOCK_STREAM
        request_queue_size = 5
        allow_reuse_address = 1
        application = None

    instance variables:
        self.server_address
        self.RequestHandlerClass
        self.__is_shut_down
        self.__shutdown_request
        self.socket
        self.server_name
        self.server_port
        self.base_environ

    class methods:
        __init__
        server_bind
        server_activate
        server_close
        serverforever # 设置 select（I/O 复用） 调用 _handle_request_noblock
        shutdown # 结束 server forever

        fileno # 返回 server socket file number

        handle_request # 使用 _handle_request_noblock 处理一次 reqeust 请求

        _handle_request_noblock # 依次调用 get_request verify_request process_request handle_error shutdown_request
        get_request # 从 server socket 中获取 client address 和 request（client socket)
        verify_request # 本示例中返回 True
        process_request # 调用 finish_request 和 shutdown_request
        finish_request # 实例化 RequestHandlerClass（处理 request 的入口
        shutdown_request #  依次调用 request.shutdown 和 close_requset
        close_request # 调用 request.close
        
        handle_error # 处理异常
        
        get_app # 获取 wsgi application
        set_app # 设置 wsgi application
        setup_environ # 设置基础 environ
    """
    application = None

    def __init__(self):
        super.__init__(self)

    def server_bind(self):
        HTTPServer.server_bind(self)
        self.setup_environ()

    def setup_environ(self):
        # 设置基础 environment
        env = self.base_environ = {}
        env["SERVER_NAME"] = self.server_name
        env["GATEWAY_INTERFACE"] = "CGI/1.1"
        env["SERVER_PORT"] = str(self.server_port)
        env["REMOTE_HOST"] = ""
        env["CONTENT_LENGTH"] = ""
        env["SCRIPT_NAME"] = ""

    def get_app(self):
        return self.application

    def set_app(self, application):
        self.application = application