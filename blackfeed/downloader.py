from concurrent.futures import ThreadPoolExecutor as PE
from requests import session as RequestSession
from requests.exceptions import RequestException
from blackfeed.helper.hasher import hashit
import os, tempfile

class Downloader:
    bulksize = 50
    session = None

    def __init__(self, adapter, multi=False, bulksize=50, stateless=True, state_id=None, verbose=False):
        self.adapter = adapter
        self.multi = multi
        self.bulksize = bulksize

        self.stateless = stateless

        self.session = RequestSession()
        self.stats = {
            'total_images': 0,
            'ignored': {
                'total': 0,
                'files': {}
            },
            'downloads': {
                'total_successes': 0,
                'total_errors': 0,
                'successes': {},
                'errors': {}
            },
            'uploads': {
                'total_successes': 0,
                'total_errors': 0,
                'successes': {},
                'errors': {}
            }
        }

        if not self.stateless:
            self.state_id = state_id
            if self.state_id is None:
                from uuid import uuid4
                self.state_id = str(uuid4())

            self.states = {}

        self.verbose = verbose

    def load_states(self, file_path):
        """ Loads states from local file """

        if self.stateless:
            print('[warning] You cannot load states in a stateless environment.')

            return False

        if not file_path.endswith('.txt'):
            file_path = '{}.txt'.format(file_path)

        if not os.path.isfile(file_path):
            raise Exception('File "{}" does not exist'.format(file_path))

        try:
            with open(file_path, 'r') as f:
                line = f.readline()
                while line:
                    checksum = line.strip()
                    destination, checksum = checksum.split(" ")
                    self.states[destination] = checksum
                    line = f.readline()
        except Exception as e:
            print('[error] Could not load states. reason: {}'.format(e))

    def process(self, queue):
        """ Function that handles a queue of file urls """

        self.stats['total_images'] = len(queue)
        if self.multi == False:
            self.handle(queue)
        else:
            self.handle_multi(queue)

        if self.stateless == False:
            self.save_states()

    def handle_multi(self, queue):
        """ Function that handles the downloads with multi-threading. """

        download_queue = []
        adapter_queue = []
        it = 0
        count = self.stats['total_images']
        for item in queue:
            download_queue.append(item)

            if (len(download_queue) % self.bulksize) == 0:
                with PE(max_workers=self.bulksize) as executor:
                    for request in executor.map(self.download, download_queue):
                        item = request['item']
                        response = request['response']

                        # If the HTTP Request was a failure
                        if not response['status']:
                            print(response)
                            exit()
                            it = self.stats['downloads']['total_errors']
                            self.stats['downloads']['errors'][it] = response
                            self.stats['downloads']['total_errors'] += 1

                            print('[error] Could not download file: "{}"'.format(item['url']))
                            it += 1

                            continue

                        # If the current and previous checksums match verification
                        if item['destination'] in self.states:
                            if self.states[item['destination']] == hashit(response['content']):
                                text = '[info] Identical file: "{}" found.'.format(item['url'])
                                if self.verbose:
                                    print(text)

                                item['message'] = text
                                index = len(self.stats['ignored']['files'])
                                self.stats['ignored']['files'][index] = item
                                self.stats['ignored']['total'] += 1
                                it += 1

                                continue

                        self.states[item['destination']] = hashit(response['content'])

                        adapter_queue.append({
                            'destination': item['destination'],
                            'body': response['content'],
                            'content-type': response['content-type']
                        })

                        del response['content']

                        it = self.stats['downloads']['total_successes']
                        self.stats['downloads']['successes'][it] = response
                        self.stats['downloads']['total_successes'] += 1
                        it += 1

                stats = self.adapter.process(adapter_queue)
                self.handle_upload_stats(stats)
                adapter_queue.clear()
                download_queue.clear()
                print('{}/{}'.format(it, count))

        # Last download trial if the queue is not empty
        if len(download_queue) > 0:
            with PE(max_workers=self.bulksize) as executor:
                for request in executor.map(self.download, download_queue):
                    item = request['item']
                    response = request['response']
                    if not response['status']:
                        it = self.stats['downloads']['total_errors']
                        self.stats['downloads']['errors'][it] = response
                        self.stats['downloads']['total_errors'] += 1

                        print('[error] Could not download file: "{}"'.format(item['url']))

                        continue

                    if item['destination'] in self.states:
                        if self.states[item['destination']] == hashit(response['content']):
                            text = '[info] Identical file: "{}" found.'.format(item['url'])
                            if self.verbose:
                                print(text)

                            item['message'] = text
                            item['content-type'] = response['content-type']

                            index = len(self.stats['ignored']['files'])
                            self.stats['ignored']['files'][index] = item
                            self.stats['ignored']['total'] += 1

                            continue

                    self.states[item['destination']] = hashit(response['content'])

                    adapter_queue.append({
                        'destination': item['destination'],
                        'body': response['content'],
                        'content-type': response['content-type']
                    })

                    response = {
                        'url': response['url'],
                        'httpcode': response['httpcode'],
                        'status': response['status'],
                        'content-type': response['content-type']
                    }
                    it = self.stats['downloads']['total_successes']
                    self.stats['downloads']['successes'][it] = response
                    self.stats['downloads']['total_successes'] += 1

            stats = self.adapter.process(adapter_queue)
            self.handle_upload_stats(stats)
            adapter_queue.clear()
            download_queue.clear()

    def handle(self, queue):
        """ Handles downloads without multithreading. """

        upload_queue = []
        for item in queue:
            try:
                download = self.download(item)
                item = download['item']
                http_response = download['response']
                if not http_response['status']:
                    print('[error] Could not download file: "{}"'.format(item['url']))
                    
                    it = self.stats['downloads']['total_errors']
                    self.stats['downloads']['errors'][it] = http_response
                    self.stats['downloads']['total_errors'] += 1

                    continue

                if item['destination'] in self.states:
                    if self.states[item['destination']] == hashit(http_response['content']):
                        text = '[info] Identical file: "{}" found.'.format(item['url'])
                        print(text)

                        item['message'] = text
                        item['content-type'] = response['content-type']

                        index = len(self.stats['ignored']['files'])
                        self.stats['ignored']['files'][index] = item
                        self.stats['ignored']['total'] += 1

                        continue

                self.states[item['destination']] = hashit(http_response['content'])

                upload_queue.append({
                    'destination': item['destination'],
                    'body': http_response['content'],
                    'content-type': http_response['content-type']
                })

                response = {
                    'url': http_response['url'],
                    'httpcode': http_response['httpcode'],
                    'status': http_response['status'],
                    'content-type': http_response['content-type']
                }

                it = self.stats['downloads']['total_successes']
                self.stats['downloads']['successes'][it] = response
                self.stats['downloads']['total_successes'] += 1
            except Exception as e:
                print('[error]', e)

        try:
            if len(upload_queue) <= 0:
                print('[warning] S3 Upload queue is empty')

                return False

            print('[info] Starting to execute adapter...')
            stats = self.adapter.process(upload_queue)
            self.handle_upload_stats(stats)

        except Exception as e:
            print('[error]', e)

    def download(self, item):
        """ Downloads a single file and returns the HTTP response. """

        if self.session is None:
            self.session = RequestSession()

        try:
            headers = { 'User-Agent': 'Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2228.0 Safari/537.3' }
            url = item['url']
            request = self.session.get(url, headers=headers)
            response = {
                'url': url,
                'httpcode': request.status_code,
                'status': request.ok,
                'content-type': request.headers.get('Content-Type')
            }
            if request.ok:
                response['content'] = request.content

            return { 'item': item, 'response': response }
        except RequestException as e:
            print('[error]', e)

            return { 'item': item, 'response': { 'status': False, 'error': e, 'url': item['url'] } }

    def handle_upload_stats(self, stats):
        """ Appends upload stats to the local stats variable. """

        total_successes = len(stats['successes'])
        total_errors = len(stats['errors'])
        self.stats['uploads']['total_successes'] += total_successes
        self.stats['uploads']['total_errors'] += total_errors

        for it, success in enumerate(stats['successes']):
            self.stats['uploads']['successes'][it] = success
        
        for it, error in enumerate(stats['errors']):
            self.stats['uploads']['errors'][it] = error

    def get_stats(self):
        """ Returns the variable containing information about the whole process. """

        return self.stats

    def save_states(self):
        """ Saves all the checksums to a file """

        outputtext = ''
        for (key, value) in self.states.items():
            outputtext += '{} {}\n'.format(key, value)

        if outputtext != '':
            with open(os.path.join(tempfile.gettempdir(), '{}.txt'.format(self.state_id)), 'w') as f:
                f.write(outputtext)

    def get_states_file(self):
        """ Returns the local file path of the checksum file. """

        return os.path.join(tempfile.gettempdir(), '{}.txt'.format(self.state_id))