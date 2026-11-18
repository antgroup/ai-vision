from unittest.mock import patch, MagicMock
from rest_framework.test import APIClient
from apps.core.tests.helpers import SpaceKeyTestCase, create_local_user, create_test_agent, create_test_space_member
from apps.core.models import AgentTypeConfig, Space

class TraceAttachmentProxyTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.owner = create_local_user('media-owner')
        self.reader = create_local_user('media-reader')
        self.space = Space.objects.create(name='media', type=2, scope=1, creator=self.owner, status=1)
        create_test_space_member(space=self.space, user=self.owner, role=1)
        create_test_space_member(space=self.space, user=self.reader, role=3)
        kind = AgentTypeConfig.objects.create(agent_name='media', agent_product='media', runtime_type='langfuse-sdk', category='CUSTOM', setup_mode='LANGFUSE', display_scope='ALL', status=1)
        self.agent = create_test_agent(space=self.space, agent_type=kind, agent_product='media', name='media', status=1)
        self.client = APIClient()
        self.url = f'/api/v1/trace-attachments/{"a" * 64}/content?space_id={self.space.uuid}&agent_uuid={self.agent.uuid}'

    def test_unauthorized_and_reader_never_request_data_plane(self):
        with patch('apps.core.views.trace_attachment.requests.get') as get:
            self.assertEqual(self.client.get(self.url).status_code, 401)
            self.client.force_authenticate(self.reader)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            get.assert_not_called()

    def test_content_is_proxied_with_scope_and_security_headers(self):
        self.client.force_authenticate(self.owner)
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.headers = {'Content-Type': 'application/pdf', 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}
        upstream.iter_content.return_value = [b'%PDF-example']
        client = MagicMock(endpoint='http://signoz', headers={'SIGNOZ-API-KEY': 'private'})
        with patch('apps.core.views.trace_attachment.media_client', return_value=(client, {'space_id': str(self.space.uuid)})), patch('apps.core.views.trace_attachment.requests.get') as get:
            get.return_value.__enter__.return_value = upstream
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b'%PDF-example')
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(get.call_args.kwargs['params']['space_id'], str(self.space.uuid))
            self.assertIn('/api/v1/ai-vision/media/', get.call_args.args[0])

    def test_cross_space_or_unbound_agent_fails_before_proxy(self):
        self.client.force_authenticate(self.owner)
        with patch('apps.core.views.trace_attachment.requests.get') as get:
            response = self.client.get(self.url.replace(str(self.agent.uuid), '00000000-0000-0000-0000-000000000000'))
            self.assertEqual(response.status_code, 404)
            get.assert_not_called()

    def test_protocol_limit_is_sixteen_mib(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get('/api/v1/trace-attachments/limits').data['data']['maxBytes'], 16 * 1024 * 1024)

    def test_upload_unwraps_signoz_reference(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.core.views.trace_attachment import TraceAttachmentUploadView
        ref = {'id': 'b' * 64, 'type': 'aivision_attachment', 'mimeType': 'application/pdf'}
        upstream = MagicMock(status_code=201, ok=True)
        upstream.json.return_value = {'status': 'success', 'data': ref}
        upstream.headers = {}
        key = MagicMock(space=self.space, id=1)
        with patch.object(TraceAttachmentUploadView, 'credentials', return_value=(key, self.agent)), patch('apps.core.views.trace_attachment.media_client', return_value=(MagicMock(endpoint='http://signoz'), {})), patch('apps.core.views.trace_attachment.requests.post', return_value=upstream), patch('apps.core.views.trace_attachment.key_cache.mark_space_key_used'):
            response = self.client.post('/api/public/trace-attachments', {'file': SimpleUploadedFile('a.pdf', b'%PDF-example', content_type='application/pdf'), 'trace_id': 'c' * 32, 'span_id': 'd' * 16, 'direction': 'input'}, format='multipart')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data, ref)
        upstream.close.assert_called_once()

    def test_input_and_output_uploads_preserve_media_type_and_direction(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.core.views.trace_attachment import TraceAttachmentUploadView

        key = MagicMock(space=self.space, id=1)
        for direction in ('input', 'output'):
            for mime, name, content in (
                ('image/png', 'image.png', b'\x89PNG\r\n\x1a\n'),
                ('audio/wav', 'audio.wav', b'RIFFaudioWAVE'),
                ('video/webm', 'video.webm', b'\x1a\x45\xdf\xa3'),
                ('application/pdf', 'report.pdf', b'%PDF-example'),
            ):
                with self.subTest(direction=direction, mime=mime):
                    ref = {'id': 'b' * 64, 'type': 'aivision_attachment', 'mimeType': mime}
                    upstream = MagicMock(status_code=201, ok=True)
                    upstream.json.return_value = {'status': 'success', 'data': ref}
                    with (
                        patch.object(TraceAttachmentUploadView, 'credentials', return_value=(key, self.agent)),
                        patch('apps.core.views.trace_attachment.media_client', return_value=(MagicMock(endpoint='http://signoz'), {})),
                        patch('apps.core.views.trace_attachment.requests.post', return_value=upstream) as post,
                        patch('apps.core.views.trace_attachment.key_cache.mark_space_key_used'),
                    ):
                        response = self.client.post('/api/public/trace-attachments', {
                            'file': SimpleUploadedFile(name, content, content_type=mime),
                            'trace_id': 'c' * 32, 'span_id': 'd' * 16, 'direction': direction,
                        }, format='multipart')
                    self.assertEqual(response.status_code, 201)
                    self.assertEqual(response.data, ref)
                    self.assertEqual(post.call_args.kwargs['data']['direction'], direction)
                    self.assertEqual(post.call_args.kwargs['files']['file'][2], mime)
                    upstream.close.assert_called_once()

    def test_exact_sixteen_mib_upload_is_forwarded_and_one_extra_byte_is_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.core.views.trace_attachment import TraceAttachmentUploadView

        limit = 16 * 1024 * 1024
        key = MagicMock(space=self.space, id=1)
        upstream = MagicMock(status_code=201, ok=True)
        upstream.json.return_value = {'status': 'success', 'data': {'id': 'b' * 64}}
        with (
            patch.object(TraceAttachmentUploadView, 'credentials', return_value=(key, self.agent)),
            patch('apps.core.views.trace_attachment.media_client', return_value=(MagicMock(endpoint='http://signoz'), {})),
            patch('apps.core.views.trace_attachment.requests.post', return_value=upstream) as post,
            patch('apps.core.views.trace_attachment.key_cache.mark_space_key_used'),
        ):
            for direction in ('input', 'output'):
                for size, status in ((limit, 201), (limit + 1, 413)):
                    with self.subTest(direction=direction, size=size):
                        post.reset_mock()
                        response = self.client.post('/api/public/trace-attachments', {
                            'file': SimpleUploadedFile('audio.wav', b'x' * size, content_type='audio/wav'),
                            'trace_id': 'c' * 32, 'span_id': 'd' * 16, 'direction': direction,
                        }, format='multipart')
                        self.assertEqual(response.status_code, status)
                        if status == 201:
                            self.assertEqual(post.call_args.kwargs['files']['file'][1].size, limit)
                        else:
                            post.assert_not_called()

    def test_oversized_data_plane_content_is_bounded_even_without_length(self):
        self.client.force_authenticate(self.owner)
        limit = 16 * 1024 * 1024
        for declared, chunks in (('16777217', []), (None, [b'x' * limit, b'x'])):
            with self.subTest(declared=declared):
                upstream = MagicMock(status_code=200)
                upstream.headers = {'Content-Type': 'video/webm'}
                if declared is not None:
                    upstream.headers['Content-Length'] = declared
                upstream.iter_content.return_value = chunks
                with (
                    patch('apps.core.views.trace_attachment.media_client', return_value=(MagicMock(endpoint='http://signoz'), {})),
                    patch('apps.core.views.trace_attachment.requests.get') as get,
                ):
                    get.return_value.__enter__.return_value = upstream
                    response = self.client.get(self.url)
                self.assertEqual(response.status_code, 413)
                if declared is not None:
                    upstream.iter_content.assert_not_called()
