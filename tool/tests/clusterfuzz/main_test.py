"""Tests the module that parses and executes commands."""
# Copyright 2016 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
import mock

from clusterfuzz import main
from test_libs import helpers


class MainTest(unittest.TestCase):
  """Test main."""

  def setUp(self):
    helpers.patch(self, [
        'clusterfuzz.commands.reproduce.execute',
        'clusterfuzz.local_logging.start_loggers'
    ])

  def test_parse_reproduce(self):
    """Test parse reproduce command."""
    main.execute(['reproduce', '1234'])
    main.execute(
        ['reproduce', '1234', '--disable-xvfb', '-j', '25', '--current',
         '--disable-goma', '-i', '500', '--target-args', '--test --test2',
         '--edit-mode', '--skip-deps', '--enable-debug', '-l', '20'])

    self.mock.start_loggers.assert_has_calls([mock.call()])
    self.mock.execute.assert_has_calls([
        mock.call(build='chromium', current=False, disable_goma=False,
                  goma_threads=None, testcase_id='1234', iterations=3,
                  disable_xvfb=False, target_args='', edit_mode=False,
                  skip_deps=False, enable_debug=False, goma_load=None),
        mock.call(build='chromium', current=True, disable_goma=True,
                  goma_threads=25, testcase_id='1234', iterations=500,
                  disable_xvfb=True, target_args='--test --test2',
                  edit_mode=True, skip_deps=True, enable_debug=True,
                  goma_load=20),
    ])
