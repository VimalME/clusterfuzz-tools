"""The main entry point."""
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

import argparse
import importlib
import logging

from clusterfuzz import common
from clusterfuzz import local_logging

logger = logging.getLogger('clusterfuzz')


def execute(argv=None):
  """The main entry point."""
  local_logging.start_loggers()
  logger.info('Version: %s', common.get_version())
  logger.info('Path: %s', __file__)

  parser = argparse.ArgumentParser(description='ClusterFuzz tools')
  subparsers = parser.add_subparsers(dest='command')

  subparsers.add_parser('supported_job_types',
                        help='List all supported job types')
  reproduce = subparsers.add_parser('reproduce', help='Reproduce a crash.')
  reproduce.add_argument('testcase_id', help='The testcase ID.')
  reproduce.add_argument(
      '-c', '--current', action='store_true', default=False,
      help=('Use the current tree; On the other hand, without --current, '
            'the Chrome repository will be switched to the commit specified in '
            'the testcase.'))
  reproduce.add_argument(
      '-b', '--build', action='store', default='chromium',
      choices=['download', 'chromium', 'standalone'],
      help='Select which type of build to run the testcase against.')
  reproduce.add_argument(
      '--disable-goma', action='store_true', default=False,
      help='Disable GOMA when building binaries locally.')
  reproduce.add_argument(
      '-j', '--goma-threads', action='store', default=None, type=int,
      help='Manually specify the number of concurrent jobs for a ninja build.')
  reproduce.add_argument(
      '-l', '--goma-load', action='store', default=None, type=int,
      help='Manually specify maximum load average for a ninja build.')
  reproduce.add_argument(
      '-i', '--iterations', action='store', default=3, type=int,
      help='Specify the number of times to attempt reproduction.')
  reproduce.add_argument(
      '-dx', '--disable-xvfb', action='store_true', default=False,
      help='Disable running testcases in a virtual frame buffer.')
  reproduce.add_argument(
      '--target-args', action='store', default='',
      help='Additional arguments for the target (e.g. chrome).')
  reproduce.add_argument(
      '--edit-mode', action='store_true', default=False,
      help='Edit args.gn before building and target arguments before running.')
  reproduce.add_argument(
      '--skip-deps', action='store_true', default=False,
      help=('Skip installing dependencies: '
            'gclient sync, gclient runhooks, install-build-deps.sh, and etc.'))
  reproduce.add_argument(
      '--enable-debug', action='store_true', default=False,
      help=(
          'Build Chrome with full debug symbols by injecting '
          '`sanitizer_keep_symbols = true` and `is_debug = true` to args.gn. '
          'Ready to debug with GDB.'))

  args = parser.parse_args(argv)
  command = importlib.import_module('clusterfuzz.commands.%s' % args.command)

  arg_dict = {k: v for k, v in vars(args).items()}
  del arg_dict['command']

  command.execute(**arg_dict)
