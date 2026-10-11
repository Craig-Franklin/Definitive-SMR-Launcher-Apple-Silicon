"""Link parsing and activation without opening any browser or network connection."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from smr_launcher.text_links import url_ranges, render_linked_text


class LinkContracts(unittest.TestCase):
    def test_punctuation_balanced_paths_and_unsafe_schemes(self):
        text = ('See (https://example.org/map). https://example.org/a_(b), '
                'https://user:password@example.org/private javascript:alert(1) file:///private/save '
                'https://github.com/owner/repo/issues/1\n')
        ranges = tuple(url_ranges(text))
        self.assertEqual([url for _, _, url in ranges], [
            'https://example.org/map', 'https://example.org/a_(b)', 'https://github.com/owner/repo/issues/1'])
        for start, end, url in ranges:
            self.assertEqual(text[start:end], url)

    def widget(self):
        widget = Mock()
        widget._url_tags = ()
        widget.cget.side_effect = lambda k: '#252527' if k == 'background' else 'xterm'
        widget.winfo_rgb.return_value = (9509, 9509, 10023)
        return widget

    def test_mouse_activation_uses_exact_target_and_replacement_removes_old_tags(self):
        widget, opener = self.widget(), Mock()
        url = 'https://example.org/map'
        render_linked_text(widget, 'Open ' + url + '.', opener=opener)
        callbacks = {(c.args[0], c.args[1]): c.args[2] for c in widget.tag_bind.call_args_list}
        self.assertEqual(callbacks[('url_0', '<Button-1>')](None), 'break')
        opener.assert_called_once_with(url)
        self.assertEqual(widget.configure.call_args.kwargs['state'], 'disabled')
        render_linked_text(widget, 'No links', opener=opener)
        widget.tag_delete.assert_called_with('url_0')

    def test_keyboard_visits_links_opens_selected_and_can_leave(self):
        widget, opener = self.widget(), Mock()
        render_linked_text(widget, 'https://example.org/1 https://example.org/2', opener=opener)
        callbacks = {c.args[0]: c.args[1] for c in widget.bind.call_args_list}
        event = SimpleNamespace(keysym='Tab', state=0)
        self.assertEqual(callbacks['<Tab>'](event), 'break')
        self.assertEqual(callbacks['<Return>'](event), 'break')
        opener.assert_called_once_with('https://example.org/1')
        self.assertEqual(callbacks['<Tab>'](event), 'break')
        self.assertEqual(callbacks['<Tab>'](event), 'break')
        widget.tk.call.assert_any_call('tk_focusNext', widget._w)
        self.assertIsNone(callbacks['<Return>'](event))
