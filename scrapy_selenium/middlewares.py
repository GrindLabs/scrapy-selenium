"""This module contains the ``SeleniumMiddleware`` scrapy middleware"""
import zipfile
from importlib import import_module

from scrapy import signals
from scrapy.exceptions import NotConfigured
from scrapy.http import HtmlResponse
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait

from .http import SeleniumRequest


class SeleniumMiddleware:
    """Scrapy middleware handling the requests using selenium"""

    def __init__(self,
                 driver_name,
                 driver_executable_path,
                 browser_executable_path,
                 command_executor,
                 driver_arguments,
                 proxy_enabled,
                 proxy_host,
                 proxy_port,
                 proxy_user,
                 proxy_pass
                 ):
        """Initialize the selenium webdriver

        Parameters
        ----------
        driver_name: str
            The selenium ``WebDriver`` to use
        driver_executable_path: str
            The path of the executable binary of the driver
        driver_arguments: list
            A list of arguments to initialize the driver
        browser_executable_path: str
            The path of the executable binary of the browser
        command_executor: str
            Selenium remote server endpoint
        proxy_enabled: bool
            Define if the requests will use proxy
        proxy_host: str
            The proxy host
        proxy_port: int
            The proxy port
        proxy_user: str
            The username to authenticate in the proxy server
        proxy_pass: str
            The password to authenticate in the proxy server
        """
        webdriver_base_path = f'selenium.webdriver.{driver_name}'

        driver_klass_module = import_module(f'{webdriver_base_path}.webdriver')
        driver_klass = getattr(driver_klass_module, 'WebDriver')

        driver_options_module = import_module(f'{webdriver_base_path}.options')
        driver_options_klass = getattr(driver_options_module, 'Options')

        driver_options = driver_options_klass()

        if browser_executable_path:
            driver_options.binary_location = browser_executable_path

        for argument in driver_arguments:
            driver_options.add_argument(argument)

        # proxy enabled
        if proxy_enabled:
            manifest_json = """
            {
                "version": "1.0.0",
                "manifest_version": 2,
                "name": "Chrome Proxy",
                "permissions": [
                    "proxy",
                    "tabs",
                    "unlimitedStorage",
                    "storage",
                    "<all_urls>",
                    "webRequest",
                    "webRequestBlocking"
                ],
                "background": {
                    "scripts": ["background.js"]
                },
                "minimum_chrome_version":"22.0.0"
            }
            """

            background_js = """
            var config = {
                mode: 'fixed_servers',
                rules: {
                    singleProxy: {
                        scheme: 'http',
                        host: '%s',
                        port: parseInt(%s)
                    },
                    bypassList: ['localhost']
                }
            };

            chrome.proxy.settings.set({value: config, scope: 'regular'}, function() {});

            function callbackFn(details) {
                return {
                    authCredentials: {
                        username: '%s',
                        password: '%s'
                    }
                };
            }

            chrome.webRequest.onAuthRequired.addListener(
                callbackFn,
                {urls: ['<all_urls>']},
                ['blocking']
            );
            """ % (proxy_host, proxy_port, proxy_user, proxy_pass)

            plugin_file = 'proxy_auth_plugin.zip'

            with zipfile.ZipFile(plugin_file, 'w') as zp:
                zp.writestr('manifest.json', manifest_json)
                zp.writestr('background.js', background_js)

            driver_options.add_extension(plugin_file)
            driver_kwargs = {
                'executable_path': driver_executable_path,
                'options': driver_options
            }
            self.driver = driver_klass(**driver_kwargs)
        # locally installed driver
        elif driver_executable_path is not None:
            driver_kwargs = {
                'executable_path': driver_executable_path,
                'options': driver_options
            }
            self.driver = driver_klass(**driver_kwargs)
        # remote driver
        elif command_executor is not None:
            capabilities = driver_options.to_capabilities()
            self.driver = webdriver.Remote(command_executor=command_executor,
                                           desired_capabilities=capabilities)

    @classmethod
    def from_crawler(cls, crawler):
        """Initialize the middleware with the crawler settings"""
        driver_name = crawler.settings.get('SELENIUM_DRIVER_NAME')
        driver_executable_path = crawler.settings.get('SELENIUM_DRIVER_EXECUTABLE_PATH')
        browser_executable_path = crawler.settings.get('SELENIUM_BROWSER_EXECUTABLE_PATH')
        command_executor = crawler.settings.get('SELENIUM_COMMAND_EXECUTOR')
        driver_arguments = crawler.settings.get('SELENIUM_DRIVER_ARGUMENTS')
        proxy_enabled = crawler.settings.get('SELENIUM_PROXY_ENABLED')
        proxy_host = crawler.settings.get('SELENIUM_PROXY_HOST')
        proxy_port = crawler.settings.get('SELENIUM_PROXY_PORT')
        proxy_user = crawler.settings.get('SELENIUM_PROXY_USER')
        proxy_pass = crawler.settings.get('SELENIUM_PROXY_PASS')

        if driver_name is None:
            raise NotConfigured('SELENIUM_DRIVER_NAME must be set')

        if driver_executable_path is None and command_executor is None:
            raise NotConfigured('Either SELENIUM_DRIVER_EXECUTABLE_PATH '
                                'or SELENIUM_COMMAND_EXECUTOR must be set')

        middleware = cls(
            driver_name=driver_name,
            driver_executable_path=driver_executable_path,
            browser_executable_path=browser_executable_path,
            command_executor=command_executor,
            driver_arguments=driver_arguments,
            proxy_enabled=proxy_enabled,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            proxy_user=proxy_user,
            proxy_pass=proxy_pass,
        )

        crawler.signals.connect(middleware.spider_closed, signals.spider_closed)

        return middleware

    def process_request(self, request, spider):
        """Process a request using the selenium driver if applicable"""
        if not isinstance(request, SeleniumRequest):
            return None

        self.driver.get(request.url)

        for cookie_name, cookie_value in request.cookies.items():
            self.driver.add_cookie(
                {
                    'name': cookie_name,
                    'value': cookie_value
                }
            )

        if request.wait_until:
            WebDriverWait(self.driver, request.wait_time).until(
                request.wait_until
            )

        if request.screenshot:
            request.meta['screenshot'] = self.driver.get_screenshot_as_png()

        if request.script:
            self.driver.execute_script(request.script)

        body = str.encode(self.driver.page_source)

        # Expose the driver via the "meta" attribute
        request.meta.update({'driver': self.driver})

        return HtmlResponse(
            self.driver.current_url,
            body=body,
            encoding='utf-8',
            request=request
        )

    def spider_closed(self):
        """Shutdown the driver when spider is closed"""
        self.driver.quit()
