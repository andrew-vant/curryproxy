import re
import urllib

import grequests

from curryproxy.errors import ConfigError
from curryproxy.responses import ErrorResponse
from curryproxy.responses import MetadataResponse
from curryproxy.responses import MultipleResponse
from curryproxy.routes.route_base import RouteBase
from curryproxy.responses import SingleResponse


ENDPOINTS_WILDCARD = '{Endpoint_IDs}'


class EndpointsRoute(RouteBase):
    def __init__(self, url_patterns, endpoints, priority_errors):
        self._url_patterns = url_patterns
        self._endpoints = {}
        self._priority_errors = priority_errors

        for endpoint_id in endpoints:
            lowered_endpoint_id = endpoint_id.lower()
            if lowered_endpoint_id in self._endpoints:
                raise ConfigError('Duplicate endpoint IDs for the same route '
                                  'are not permitted.')
            self._endpoints[lowered_endpoint_id] = endpoints[endpoint_id]

    def __call__(self, request):
        original_request = request.copy()

        destination_urls = self._create_forwarded_urls(request.url)

        # Use gzip even if the original requestor didn't support it
        request.headers['Accept-Encoding'] = 'gzip,identity'
        # Host header is automatically added for each request by grequests
        del request.headers['Host']

        requests = (grequests.request(request.method,
                                      destination_url,
                                      data=request.body,
                                      headers=request.headers,
                                      allow_redirects=False,
                                      verify=True)
                    for destination_url in destination_urls)
        requests_responses = grequests.map(requests, stream=True)

        response = None
        if ('Proxy-Aggregator-Body' in original_request.headers
                and original_request.headers['Proxy-Aggregator-Body'].lower()
                == 'response-metadata'):
            response = MetadataResponse(original_request, requests_responses)
        elif len(requests_responses) == 1:
            response = SingleResponse(original_request, requests_responses[0])
        elif any(r.status_code >= 400 for r in requests_responses):
            response = ErrorResponse(original_request,
                                     requests_responses,
                                     self._priority_errors)
        else:
            response = MultipleResponse(original_request, requests_responses)

        return response.response

    def _create_forwarded_urls(self, request_url):
        # Extract endpoints from request
        url_pattern = self._find_pattern_for_request(request_url)
        url_pattern_parts = url_pattern.split(ENDPOINTS_WILDCARD)
        match_expression = re.escape(url_pattern_parts[0]) + \
            "(?P<endpoint_ids>.*)" + \
            re.escape(url_pattern_parts[1])
        endpoint_ids = re.match(match_expression, request_url)

        # Extract trailing portion of URL
        trailing_route = request_url[len(url_pattern_parts[0]
                                         + endpoint_ids.group("endpoint_ids")
                                         + url_pattern_parts[1]):]

        # Create final URLs to be forwarded
        endpoint_urls = []
        for endpoint_id in endpoint_ids.group("endpoint_ids").split(','):
            endpoint_id = urllib.unquote(endpoint_id)
            url = self._endpoints[endpoint_id.strip().lower()] + trailing_route
            endpoint_urls.append(url)

        return endpoint_urls

    def _find_pattern_for_request(self, request_url):
        wildcard_escaped = re.escape(ENDPOINTS_WILDCARD)

        for url_pattern in self._url_patterns:
            pattern_escaped = re.escape(url_pattern)
            pattern_escaped = pattern_escaped.replace(wildcard_escaped,
                                                      '.*',
                                                      1)

            if re.match(pattern_escaped, request_url, re.IGNORECASE):
                return url_pattern

        return None
