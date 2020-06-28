#!/usr/bin/env python
# -*- coding: utf-8 -*-
#  SPDX-License-Identifier: Apache-2.0
"""
Python Package for controlling Alexa devices (echo dot, etc) programmatically.

For more details about this api, please refer to the documentation at
https://gitlab.com/keatontaylor/alexapy
"""

from json import JSONDecodeError
import logging
from typing import Callable, List, Optional, Text, Tuple, Union
from typing import Dict  # noqa pylint: disable=unused-import
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from simplejson import JSONDecodeError as SimpleJSONDecodeError

from alexapy import aiohttp
from alexapy.aiohttp.client_exceptions import ContentTypeError

from .const import EXCEPTION_TEMPLATE
from .helpers import _catch_all_exceptions

_LOGGER = logging.getLogger(__name__)


class AlexaLogin:
    # pylint: disable=too-many-instance-attributes
    """Class to handle login connection to Alexa. This class will not reconnect.

    Args:
    url (string): Localized Amazon domain (e.g., amazon.com)
    email (string): Amazon login account
    password (string): Password for Amazon login account
    outputpath (function): Local path with write access for storing files
    debug (boolean): Enable additional debugging including debug file creation

    """

    def __init__(
        self,
        url: Text,
        email: Text,
        password: Text,
        outputpath: Callable[[Text], Text],
        debug: bool = False,
    ) -> None:
        # pylint: disable=too-many-arguments,import-outside-toplevel
        """Set up initial connection and log in."""
        import ssl
        import certifi

        prefix: Text = "alexa_media"
        self._prefix = "https://alexa."
        self._url: Text = url
        self._email: Text = email
        self._password: Text = password
        self._session: Optional[aiohttp.ClientSession] = None
        self._ssl = ssl.create_default_context(
            purpose=ssl.Purpose.SERVER_AUTH, cafile=certifi.where()
        )
        self._cookies: Optional[Dict[Text, Text]] = {}
        self._headers: Dict[Text, Text] = {}
        self._data: Optional[Dict[Text, Text]] = None
        self.status: Optional[Dict[Text, Union[Text, bool]]] = {}
        self._cookiefile: List[Text] = [
            outputpath(".storage/{}.{}.pickle".format(prefix, email)),
            outputpath("{}.{}.pickle".format(prefix, email)),
        ]
        self._debugpost: Text = outputpath("{}{}post.html".format(prefix, email))
        self._debugget: Text = outputpath("{}{}get.html".format(prefix, email))
        self._lastreq: Optional[aiohttp.ClientResponse] = None
        self._debug: bool = debug
        self._links: Optional[Dict[Text, Tuple[Text, Text]]] = {}
        self._options: Optional[Dict[Text, Text]] = {}
        self._site: Optional[Text] = None
        self._create_session()

    @property
    def email(self) -> Text:
        """Return email or mobile account for this Login."""
        return self._email

    @property
    def session(self) -> Optional[aiohttp.ClientSession]:
        """Return session for this Login."""
        return self._session

    @property
    def url(self) -> Text:
        """Return session for this Login."""
        return self._url

    @property
    def links(self) -> Text:
        """Return string list of links from last page for this Login."""
        result = ""
        assert self._links is not None
        for key, value in self._links.items():
            result += f"link{key}:{value[0]}\n"
        return result

    async def login_with_cookie(self) -> None:
        # pylint: disable=import-outside-toplevel
        """Attempt to login after loading cookie."""
        import pickle
        import os
        import aiofiles
        from requests.cookies import RequestsCookieJar
        from collections import defaultdict

        cookies: Optional[RequestsCookieJar] = None
        numcookies: int = 0
        loaded: bool = False
        if self._cookiefile:
            for cookiefile in self._cookiefile:
                numcookies = 0
                if not os.path.exists(cookiefile):
                    continue
                if loaded and cookiefile != self._cookiefile[0]:
                    _LOGGER.debug("Deleting old cookiefile %s ", cookiefile)
                    try:
                        from aiofiles import os

                        await os.remove(cookiefile)
                    except (OSError, EOFError, TypeError, AttributeError) as ex:
                        _LOGGER.debug(
                            "Error deleting cookie: %s",
                            EXCEPTION_TEMPLATE.format(type(ex).__name__, ex.args),
                        )
                _LOGGER.debug("Trying to load pickled cookie from file %s", cookiefile)
                try:
                    async with aiofiles.open(cookiefile, "rb") as myfile:
                        cookies = pickle.loads(await myfile.read())
                        if self._debug:
                            _LOGGER.debug(
                                "cookie loaded: %s %s", type(cookies), cookies
                            )
                except (OSError, EOFError, pickle.UnpicklingError) as ex:
                    _LOGGER.debug(
                        "Error loading pickled cookie from %s: %s",
                        cookiefile,
                        EXCEPTION_TEMPLATE.format(type(ex).__name__, ex.args),
                    )
                # escape extra quote marks from Requests cookie
                if isinstance(cookies, RequestsCookieJar):
                    _LOGGER.debug("Loading RequestsCookieJar")
                    cookies = cookies.get_dict()
                    assert self._cookies is not None
                    assert cookies is not None
                    for key, value in cookies.items():
                        if self._debug:
                            _LOGGER.debug('Key: "%s", Value: "%s"', key, value)
                        self._cookies[str(key)] = value.strip('"')
                    numcookies = len(self._cookies)
                elif isinstance(cookies, defaultdict):
                    _LOGGER.debug("Trying to load aiohttpCookieJar to session")
                    cookie_jar: aiohttp.CookieJar = self._session.cookie_jar
                    try:
                        cookie_jar.load(cookiefile)
                        self._prepare_cookies_from_session(self._url)
                        numcookies = len(self._cookies)
                    except (OSError, EOFError, TypeError, AttributeError) as ex:
                        _LOGGER.debug(
                            "Error loading aiohttpcookie from %s: %s",
                            cookiefile,
                            EXCEPTION_TEMPLATE.format(type(ex).__name__, ex.args),
                        )
                        # a cookie_jar.load error can corrupt the session
                        # so we must recreate it
                        self._create_session(True)
                elif isinstance(cookies, dict):
                    _LOGGER.debug("Found dict cookie")
                    self._cookies = cookies
                    numcookies = len(self._cookies)
                else:
                    _LOGGER.debug("Ignoring unknown file %s", type(cookies))
                if numcookies:
                    _LOGGER.debug("Loaded %s cookies", numcookies)
                    loaded = True
                    if cookiefile != self._cookiefile[0]:
                        _LOGGER.debug(
                            "Migrating old cookiefile to %s ", self._cookiefile[0]
                        )
                        try:
                            from aiofiles import os

                            await os.rename(cookiefile, self._cookiefile[0])
                        except (OSError, EOFError, TypeError, AttributeError) as ex:
                            _LOGGER.debug(
                                "Error renaming cookie: %s",
                                EXCEPTION_TEMPLATE.format(type(ex).__name__, ex.args),
                            )
        await self.login(cookies=self._cookies)

    async def close(self) -> None:
        """Close connection for login."""
        if self._session and not self._session.closed:
            if self._session._connector_owner:
                assert self._session._connector is not None
                await self._session._connector.close()
            self._session._connector = None

    async def reset(self) -> None:
        # pylint: disable=import-outside-toplevel
        """Remove data related to existing login."""
        await self.close()
        self._session = None
        self._cookies = {}
        self._data = None
        self._lastreq = None
        self.status = {}
        self._links = {}
        self._options = {}
        self._site = None
        self._create_session()
        import os
        from aiofiles import os as aioos

        for cookiefile in self._cookiefile:
            if (cookiefile) and os.path.exists(cookiefile):
                try:
                    _LOGGER.debug("Trying to delete cookie file %s", cookiefile)
                    await aioos.remove(cookiefile)
                except OSError as ex:
                    _LOGGER.debug(
                        "Error deleting cookie %s: %s",
                        cookiefile,
                        EXCEPTION_TEMPLATE.format(type(ex).__name__, ex.args),
                    )

    @classmethod
    def get_inputs(cls, soup: BeautifulSoup, searchfield=None) -> Dict[str, str]:
        """Parse soup for form with searchfield."""
        searchfield = searchfield or {"name": "signIn"}
        data = {}
        form = soup.find("form", searchfield)
        if not form:
            form = soup.find("form")
        for field in form.find_all("input"):
            try:
                data[field["name"]] = ""
                if field["type"] and field["type"] == "hidden":
                    data[field["name"]] = field["value"]
            except BaseException:  # pylint: disable=broad-except
                pass
        return data

    async def test_loggedin(self, cookies: Union[Dict[str, str], None] = None) -> bool:
        # pylint: disable=import-outside-toplevel
        """Function that will test the connection is logged in.

        Tests:
        - Attempts to get authenticaton and compares to expected login email
        Returns false if unsuccesful getting json or the emails don't match
        - Checks for existence of csrf cookie
        Returns false if no csrf found; necessary to issue commands
        """
        if self._debug:
            from json import dumps

            _LOGGER.debug("Testing whether logged in to alexa.%s", self._url)
            _LOGGER.debug("Cookies: %s", cookies)
            _LOGGER.debug("Session Cookies:\n%s", self._print_session_cookies())
            _LOGGER.debug("Header: %s", dumps(self._headers))
        if not self._session:
            self._create_session()
        get_resp = await self._session.get(
            self._prefix + self._url + "/api/bootstrap", cookies=cookies, ssl=self._ssl
        )
        await self._process_resp(get_resp)
        try:
            json = await get_resp.json()
            email = json["authentication"]["customerEmail"]
        except (JSONDecodeError, SimpleJSONDecodeError, ContentTypeError) as ex:
            _LOGGER.debug(
                "Not logged in: %s",
                EXCEPTION_TEMPLATE.format(type(ex).__name__, ex.args),
            )
            return False
        if email != "" and email.lower() == self._email.lower():
            _LOGGER.debug("Logged in as %s", email)
            return True
        if email == "":
            _LOGGER.debug("Logged in as mobile account %s", email)
            return True
        _LOGGER.debug("Not logged in due to email mismatch")
        await self.reset()
        return False

    def _create_session(self, force=False) -> None:
        if not self._session or force:
            #  define session headers
            self._headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 6.3; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/68.0.3440.106 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml, "
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "*",
            }

            #  initiate session
            self._session = aiohttp.ClientSession(headers=self._headers)

    def _prepare_cookies_from_session(self, site: Text) -> None:
        """Update self._cookies from aiohttp session.

        This should only be needed to run after a succesful login.
        """
        assert self._session
        cookie_jar = self._session.cookie_jar
        if self._cookies is None:
            self._cookies = {}
        if self._debug:
            _LOGGER.debug(
                "Updating self._cookies with %s session cookies:\n%s",
                site,
                self._print_session_cookies(),
            )
        for cookie in cookie_jar:
            oldvalue = self._cookies[cookie.key] if cookie.key in self._cookies else ""
            if cookie["domain"] == str(site):
                self._cookies[cookie.key] = cookie.value
                if self._debug:
                    _LOGGER.debug(
                        "%s: key: %s value: %s -> %s",
                        site,
                        cookie.key,
                        oldvalue,
                        cookie.value,
                    )

    def _print_session_cookies(self) -> Text:
        result: Text = ""
        if not self._session.cookie_jar:
            result = "Session cookie jar is empty."
        for cookie in self._session.cookie_jar:
            result += "{}: expires:{} max-age:{} {}={}\n".format(
                cookie["domain"],
                cookie["expires"],
                cookie["max-age"],
                cookie.key,
                cookie.value,
            )
        return result

    @_catch_all_exceptions
    async def login(
        self,
        cookies: Optional[Dict[Text, Text]] = None,
        data: Optional[Dict[Text, Optional[Text]]] = None,
    ) -> None:
        # pylint: disable=too-many-branches,too-many-locals,
        # pylint: disable=too-many-statements
        """Login to Amazon."""
        data = data or {}
        if cookies:
            _LOGGER.debug("Using cookies to log in")
            if await self.test_loggedin(cookies):
                self.status = {}
                self.status["login_successful"] = True
                _LOGGER.debug("Log in successful with cookies")
                self._prepare_cookies_from_session(self._url)
                return
            await self.reset()
        _LOGGER.debug("No valid cookies for log in; using credentials")
        #  site = 'https://www.' + self._url + '/gp/sign-in.html'
        #  use alexa site instead
        if not self._site:
            site: Text = self._prefix + self._url
        else:
            site = self._site
        if not self._session:
            self._create_session()
        #  This will process links which is used for debug only to force going
        #  to other links.  Warning, chrome will cache any link parameters
        #  breaking the configuration flow until refresh on browser.
        digit = None
        for datum, value in data.items():
            if (
                value
                and str(value).startswith("link")
                and len(value) > 4
                and value[4:].isdigit()
            ):
                digit = str(value[4:])
                _LOGGER.debug("Found link selection %s in %s ", digit, datum)
                assert self._links is not None
                if self._links.get(digit):
                    (text, site) = self._links[digit]
                    data[datum] = None
                    _LOGGER.debug("Going to link with text: %s href: %s ", text, site)
                    _LOGGER.debug("%s reset to %s ", datum, data[datum])
        if not digit and self._lastreq is not None:
            assert self._lastreq is not None
            site = str(self._lastreq.url)
            _LOGGER.debug("Loaded last request to %s ", site)
            resp = self._lastreq
        else:
            resp = await self._session.get(site, headers=self._headers, ssl=self._ssl)
            self._lastreq = resp
            site = await self._process_resp(resp)
        html: Text = await resp.text()
        if self._debug:
            import aiofiles

            async with aiofiles.open(self._debugget, mode="wb") as localfile:
                await localfile.write(await resp.read())
        # This commented block can be used to read a file directly to process.
        # import aiofiles

        # async with aiofiles.open("/config/anti-automation.html", "rb") as myfile:
        #     html = await myfile.read()
        site = await self._process_page(html, site)
        if not self.status.get("force_get"):
            missing_params = self._populate_data(site, data)
            if self._debug:
                from json import dumps  # pylint: disable=import-outside-toplevel

                if missing_params:
                    _LOGGER.debug(
                        "WARNING: Detected missing params: %s", missing_params
                    )
                _LOGGER.debug("Session Cookies:\n%s", self._print_session_cookies())
                _LOGGER.debug("Submit Form Data: %s", dumps(self._data))
                _LOGGER.debug("Header: %s", dumps(self._headers))

            # submit post request with username/password and other needed info
            post_resp = await self._session.post(
                site, data=self._data, headers=self._headers, ssl=self._ssl,
            )
            # headers need to be submitted to have the referer
            if self._debug:
                import aiofiles

                async with aiofiles.open(self._debugpost, mode="wb") as localfile:
                    await localfile.write(await post_resp.read())
            self._lastreq = post_resp
            site = await self._process_resp(post_resp)
            self._site = await self._process_page(await post_resp.text(), site)

    async def _process_resp(self, resp) -> Text:
        if resp.history:
            for item in resp.history:
                _LOGGER.debug("%s: redirected from\n%s", item.method, item.url)
            self._headers["Referer"] = str(resp.url)
        url = resp.request_info.url
        method = resp.request_info.method
        status = resp.status
        reason = resp.reason
        headers = resp.request_info.headers
        if self._debug:
            _LOGGER.debug(
                "%s: \n%s with\n%s\n returned %s:%s with response %s",
                method,
                url,
                headers,
                status,
                reason,
                resp.headers,
            )
        else:
            _LOGGER.debug(
                "%s: \n%s returned %s:%s with response %s",
                method,
                url,
                status,
                reason,
                resp.headers,
            )
        self._headers["Referer"] = str(url)
        return url

    async def _process_page(self, html: str, site: Text) -> Text:
        # pylint: disable=too-many-branches,too-many-locals,
        # pylint: disable=too-many-statements
        # pylint: disable=import-outside-toplevel
        """Process html to set login.status and find form post url."""

        def find_links() -> None:
            links = {}
            index = 0
            if links_tag:
                for link in links_tag:
                    if not link.string:
                        continue
                    string = link.string.strip()
                    href = link["href"]
                    # _LOGGER.debug("Found link: %s <%s>",
                    #               string,
                    #               href)
                    if href.startswith("/"):
                        links[str(index)] = (string, (self._prefix + self._url + href))
                        index += 1
                    elif href.startswith("http"):
                        links[str(index)] = (string, href)
                        index += 1
            if forms_tag:
                for form in forms_tag:
                    if (
                        form.get("method")
                        and form.get("method") == "get"
                        and form.get("action")
                    ):
                        string = form.get("id")
                        action = form.get("action")
                        params = {}
                        inputs = form.findAll("input")
                        for item in inputs:
                            if (
                                item
                                and item.get("type")
                                and item.get("type") == "hidden"
                            ):
                                params[item.get("name")] = item.get("value")
                        href = f"{self._prefix}{self._url}{action}?{urlencode(params)}"
                        links[str(index)] = (string, href)
                        index += 1
            if links:
                _LOGGER.debug("Links: %s", links)
            self._links = links

        _LOGGER.debug("Processing %s", site)
        soup: BeautifulSoup = BeautifulSoup(html, "html.parser")

        status: Dict[Text, Union[Text, bool]] = {}

        #  Find tags to determine which path
        login_tag = soup.find("form", {"name": "signIn"})
        captcha_tag = soup.find(id="auth-captcha-image")
        securitycode_tag = soup.find(id="auth-mfa-otpcode")
        errorbox = (
            soup.find(id="auth-error-message-box")
            if soup.find(id="auth-error-message-box")
            else soup.find(id="auth-warning-message-box")
        )
        claimspicker_tag = soup.find("form", {"name": "claimspicker"})
        authselect_tag = soup.find("form", {"id": "auth-select-device-form"})
        verificationcode_tag = soup.find("form", {"action": "verify"})
        verification_captcha_tag = soup.find("img", {"alt": "captcha"})
        javascript_authentication_tag = soup.find("form", {"id": "pollingForm"})
        links_tag = soup.findAll("a", href=True)
        forms_tag = soup.findAll("form")
        form_tag = soup.find("form")
        missingcookies_tag = soup.find(id="ap_error_return_home")
        if self._debug:
            find_links()

        # pull out Amazon error message

        if errorbox:
            error_message = errorbox.find("h4").string
            for list_item in errorbox.findAll("li"):
                error_message += list_item.find("span").string
            _LOGGER.debug("Error message: %s", error_message)
            status["error_message"] = error_message

        if login_tag and not captcha_tag:
            _LOGGER.debug("Found standard login page")
            #  scrape login page to get all the inputs required for login
            self._data = self.get_inputs(soup, {"name": "signIn"})
        elif captcha_tag is not None:
            _LOGGER.debug("Captcha requested")
            status["captcha_required"] = True
            status["captcha_image_url"] = captcha_tag.get("src")
            self._data = self.get_inputs(soup)

        elif securitycode_tag is not None:
            _LOGGER.debug("2FA requested")
            status["securitycode_required"] = True
            self._data = self.get_inputs(soup, {"id": "auth-mfa-form"})

        elif claimspicker_tag is not None:
            self._options = {}
            index = 0
            claims_message = ""
            options_message = ""
            for div in claimspicker_tag.findAll("div", "a-row"):
                claims_message += f"{div.text}\n"
            for label in claimspicker_tag.findAll("label"):
                value = (
                    (label.find("input")["value"]).strip()
                    if label.find("input")
                    else ""
                )
                message = (
                    (label.find("span").string).strip() if label.find("span") else ""
                )
                valuemessage = (
                    (f"* **`{index}`**:\t `{value} - {message}`.\n")
                    if value != ""
                    else ""
                )
                options_message += valuemessage
                if value:
                    self._options[str(index)] = value
                    index += 1
            _LOGGER.debug(
                "Verification method requested: %s, %s", claims_message, options_message
            )
            status["claimspicker_required"] = True
            status["claimspicker_message"] = options_message
            self._data = self.get_inputs(soup, {"name": "claimspicker"})
        elif authselect_tag is not None:
            self._options = {}
            index = 0
            authselect_message = ""
            authoptions_message = ""
            for div in soup.findAll("div", "a-box-inner"):
                if div.find("p"):
                    authselect_message += f"{div.find('p').string}\n"
            for label in authselect_tag.findAll("label"):
                value = (
                    (label.find("input")["value"]).strip()
                    if label.find("input")
                    else ""
                )
                message = (
                    (label.find("span").string).strip() if label.find("span") else ""
                )
                valuemessage = (f"{index}:\t{message}\n") if value != "" else ""
                authoptions_message += valuemessage
                if value:
                    self._options[str(index)] = value
                    index += 1
            _LOGGER.debug(
                "OTP method requested: %s%s", authselect_message, authoptions_message
            )
            status["authselect_required"] = True
            status["authselect_message"] = authoptions_message
            self._data = self.get_inputs(soup, {"id": "auth-select-device-form"})
        elif verification_captcha_tag is not None:
            _LOGGER.debug("Verification captcha code requested:")
            status["captcha_required"] = True
            status["captcha_image_url"] = verification_captcha_tag.get("src")
            status["verification_captcha_required"] = True
            self._data = self.get_inputs(soup, {"action": "verify"})
        elif verificationcode_tag is not None:
            _LOGGER.debug("Verification code requested:")
            status["verificationcode_required"] = True
            self._data = self.get_inputs(soup, {"action": "verify"})
        elif missingcookies_tag is not None:
            _LOGGER.debug("Error page detected:")
            href = ""
            links = missingcookies_tag.findAll("a", href=True)
            for link in links:
                href = link["href"]
            status["ap_error"] = True
            status["force_get"] = True
            status["ap_error_href"] = href
        elif javascript_authentication_tag:
            import re

            message: Text = ""

            message = soup.find("span").getText()
            for div in soup.findAll("div", {"id": "channelDetails"}):
                message += div.getText()
            status["force_get"] = True
            status["message"] = re.sub("(\\s)+", "\\1", message)
            _LOGGER.debug("Javascript Authentication page detected: %s", message)
        else:
            _LOGGER.debug("Captcha/2FA not requested; confirming login.")
            if await self.test_loggedin():
                _LOGGER.debug(
                    "Login confirmed; saving cookie to %s", self._cookiefile[0]
                )
                status["login_successful"] = True
                self._prepare_cookies_from_session(self._url)
                if self._debug:
                    _LOGGER.debug("Saving cookie: %s", self._print_session_cookies())
                for cookiefile in self._cookiefile:
                    try:
                        import os
                        from aiofiles import os as aioos

                        if cookiefile == self._cookiefile[0]:
                            cookie_jar = self._session.cookie_jar
                            assert isinstance(cookie_jar, aiohttp.CookieJar)
                            cookie_jar.save(self._cookiefile[0])
                        elif (cookiefile) and os.path.exists(cookiefile):
                            _LOGGER.debug("Removing outdated cookiefile %s", cookiefile)
                            await aioos.remove(cookiefile)
                    except OSError as ex:
                        _LOGGER.debug(
                            "Error saving pickled cookie to %s: %s",
                            self._cookiefile[0],
                            EXCEPTION_TEMPLATE.format(type(ex).__name__, ex.args),
                        )
                #  remove extraneous Content-Type to avoid 500 errors
                self._headers.pop("Content-Type", None)

            else:
                _LOGGER.debug("Login failed; check credentials")
                status["login_failed"] = True
                assert self._data is not None
                if "" in self._data.values():
                    missing = [k for (k, v) in self._data.items() if v == ""]
                    _LOGGER.debug(
                        "If credentials correct, please report"
                        " these missing values: %s",
                        missing,
                    )
        self.status = status
        # determine post url if not logged in
        if form_tag and "login_successful" not in status:
            formsite: Text = form_tag.get("action")
            if self._debug:
                _LOGGER.debug("Found form to process: %s", form_tag)
            if formsite and formsite == "verify":
                import re

                search_results = re.search(r"(.+)/(.*)", str(site))
                assert search_results is not None
                site = search_results.groups()[0] + "/verify"
                _LOGGER.debug("Found post url to verify; converting to %s", site)
            elif formsite and formsite == "get":
                if "ap_error" in status and status.get("ap_error_href"):
                    assert isinstance(status["ap_error_href"], str)
                    site = status["ap_error_href"]
                else:
                    site = self._headers["Referer"]
                _LOGGER.debug("Found post url to get; forcing get to %s", site)
                self._lastreq = None
            elif formsite and formsite == "/ap/cvf/approval/poll":
                site = form_tag.find("input", {"name": "openid.return_to"}).get("value")
                _LOGGER.debug("Found url for openid.return_to %s", site)
            elif formsite:
                site = formsite
                _LOGGER.debug("Found post url to %s", site)
        return site

    def _populate_data(self, site: Text, data: Dict[str, Optional[str]]) -> bool:
        """Populate self._data with info from data."""
        # pull data from configurator
        password: Optional[Text] = (
            None if "password" not in data else data["password"]
        )
        captcha: Optional[Text] = (None if "captcha" not in data else data["captcha"])
        securitycode: Optional[Text] = (
            None if "securitycode" not in data else data["securitycode"]
        )
        claimsoption: Optional[Text] = (
            None if "claimsoption" not in data else data["claimsoption"]
        )
        authopt: Optional[Text] = (
            None if "authselectoption" not in data else data["authselectoption"]
        )
        verificationcode: Optional[Text] = (
            None if "verificationcode" not in data else data["verificationcode"]
        )
        _LOGGER.debug("Preparing post to %s with input data: %s", site, data)

        #  add username and password to the data for post request
        #  check if there is an input field
        if self._data:
            if "email" in self._data and self._data["email"] == "":
                self._data["email"] = self._email
            if "password" in self._data and self._data["password"] == "":
                self._data["password"] = self._password if not password else password
            if "rememberMe" in self._data:
                self._data["rememberMe"] = "true"
            if captcha is not None and "guess" in self._data:
                self._data["guess"] = captcha
            if captcha is not None and "cvf_captcha_input" in self._data:
                self._data["cvf_captcha_input"] = captcha
                self._data["cvf_captcha_captcha_action"] = "verifyCaptcha"
            if securitycode is not None and "otpCode" in self._data:
                self._data["otpCode"] = securitycode
                self._data["rememberDevice"] = "true"
            if claimsoption is not None and "option" in self._data:
                try:
                    self._data["option"] = self._options[str(claimsoption)]
                except KeyError:
                    _LOGGER.debug(
                        "Selected claimspicker option %s not in %s",
                        str(claimsoption),
                        self._options,
                    )
            if authopt is not None and "otpDeviceContext" in self._data:
                try:
                    self._data["otpDeviceContext"] = self._options[str(authopt)]
                except KeyError:
                    _LOGGER.debug(
                        "Selected OTP option %s not in %s", str(authopt), self._options,
                    )
            if verificationcode is not None and "code" in self._data:
                self._data["code"] = verificationcode
            self._data.pop("", None)  # remove '' key
            return "" in self._data.values()  # test if unfilled values
        return False
