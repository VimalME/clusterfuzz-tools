"""Classes to download, build and provide binaries for reproduction."""
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

import base64
import json
import logging
import multiprocessing
import os
import stat
import string
import urllib

import urlfetch

from clusterfuzz import common
from clusterfuzz import output_transformer
from error import error


CHECKOUT_MESSAGE = (
    'We want to checkout to the revision {revision}.\n'
    "If you wouldn't like to perform the checkout, "
    'please re-run with --current.\n'
    'Shall we proceed with the following command:\n'
    '{cmd} in {source_dir}?')
ARGS_GN_FILENAME = 'args.gn'


logger = logging.getLogger('clusterfuzz')


def build_revision_to_sha_url(revision, repo):
  return ('https://cr-rev.appspot.com/_ah/api/crrev/v1/get_numbering?%s' %
          urllib.urlencode({
              'number': revision,
              'numbering_identifier': 'refs/heads/master',
              'numbering_type': 'COMMIT_POSITION',
              'project': 'chromium',
              'repo': repo}))


def sha_from_revision(revision, repo):
  """Converts a chrome revision number to it corresponding git sha."""

  response = urlfetch.fetch(build_revision_to_sha_url(revision, repo))
  return json.loads(response.body)['git_sha']


def get_pdfium_sha(chromium_sha):
  """Gets the correct Pdfium sha using the Chromium sha."""
  response = urlfetch.fetch(
      ('https://chromium.googlesource.com/chromium/src.git/+/%s/DEPS?'
       'format=TEXT' % chromium_sha))
  body = base64.b64decode(response.body)
  sha_line = [l for l in body.split('\n') if "'pdfium_revision':" in l][0]
  sha_line = sha_line.translate(None, string.punctuation).replace(
      'pdfiumrevision', '')
  return sha_line.strip()


def sha_exists(sha, source_dir):
  """Check if sha exists."""
  returncode, _ = common.execute(
      'git', 'cat-file -e %s' % sha, cwd=source_dir, exit_on_error=False)
  return returncode == 0


def ensure_sha(sha, source_dir):
  """Ensure the sha exists."""
  if sha_exists(sha, source_dir):
    return

  common.execute('git', 'fetch origin %s' % sha, source_dir)


def is_repo_dirty(path):
  """Returns true if the source dir has uncommitted changes."""
  # `git diff` always return 0 (even when there's change).
  _, diff_result = common.execute(
      'git', 'diff', path, print_command=False, print_output=False)
  return bool(diff_result)


def get_current_sha(source_dir):
  """Return the current sha."""
  _, current_sha = common.execute(
      'git', 'rev-parse HEAD', source_dir, print_command=False,
      print_output=False)
  return current_sha.strip()


def setup_debug_symbol_if_needed(gn_args, sanitizer, enable_debug):
  """Setup debug symbol if enable_debug is true. See: crbug.com/692620"""
  if not enable_debug:
    return gn_args

  gn_args['sanitizer_keep_symbols'] = 'true'
  gn_args['symbol_level'] = '2'

  if sanitizer != 'MSAN':
    gn_args['is_debug'] = 'true'
  return gn_args


def install_build_deps_32bit(source_dir):
  """Run install-build-deps.sh."""
  # preexec_fn is required to be None. Otherwise, it'd fail with:
  # 'sudo: no tty present and no askpass program specified'.
  common.execute(
      'build/install-build-deps.sh', '--lib32 --syms --no-prompt',
      source_dir, stdout_transformer=output_transformer.Identity(),
      preexec_fn=None, redirect_stderr_to_stdout=True)


def gclient_runhooks_msan(source_dir, msan_track_origins):
  """Run gclient runhooks for msan."""
  common.execute(
      'gclient', 'runhooks', source_dir,
      env={
          'GYP_DEFINES': (
              'msan=1 msan_track_origins=%s '
              'use_prebuilt_instrumented_libraries=1'
              % (msan_track_origins or '2'))
      }
  )


def read_gn_args(gn_args, downloaded_args_gn_path):
  """Read gn_args from variable if exist. Otherwise, get it from file."""
  if gn_args:
    return gn_args

  with open(downloaded_args_gn_path, 'r') as f:
    return f.read()


class BinaryProvider(object):
  """Downloads/builds and then provides the location of a binary."""

  def __init__(self, testcase_id, build_url, binary_name):
    self.testcase_id = testcase_id
    self.build_url = build_url
    self.build_directory = None
    self.binary_name = binary_name

  def get_build_directory(self):
    """Get build directory. This method must be implemented by a subclass."""
    raise NotImplementedError

  def download_build_data(self):
    """Downloads a build and saves it locally."""

    build_dir = self.build_dir_name()
    binary_location = os.path.join(build_dir, self.binary_name)
    if os.path.exists(build_dir):
      return build_dir

    logger.info('Downloading build data...')
    if not os.path.exists(common.CLUSTERFUZZ_BUILDS_DIR):
      os.makedirs(common.CLUSTERFUZZ_BUILDS_DIR)

    gsutil_path = self.build_url.replace(
        'https://storage.cloud.google.com/', 'gs://')
    common.gsutil('cp %s .' % gsutil_path, common.CLUSTERFUZZ_CACHE_DIR)

    filename = os.path.split(gsutil_path)[1]
    saved_file = os.path.join(common.CLUSTERFUZZ_CACHE_DIR, filename)

    common.execute(
        'unzip', '-q %s -d %s' % (saved_file, common.CLUSTERFUZZ_BUILDS_DIR),
        cwd=common.CLUSTERFUZZ_DIR)

    logger.info('Cleaning up...')
    os.remove(saved_file)
    os.rename(os.path.join(common.CLUSTERFUZZ_BUILDS_DIR,
                           os.path.splitext(filename)[0]), build_dir)
    stats = os.stat(binary_location)
    os.chmod(binary_location, stats.st_mode | stat.S_IEXEC)

  def get_binary_path(self):
    return '%s/%s' % (self.get_build_directory(), self.binary_name)

  def build_dir_name(self):
    """Returns a build number's respective directory."""
    return os.path.join(common.CLUSTERFUZZ_BUILDS_DIR,
                        str(self.testcase_id) + '_build')


class DownloadedBinary(BinaryProvider):
  """Uses a downloaded binary."""

  def get_build_directory(self):
    """Returns the location of the correct build to use for reproduction."""

    if self.build_directory:
      return self.build_directory

    self.download_build_data()
    # We need the source dir so we can use asan_symbolize.py from the
    # chromium source directory.
    self.source_directory = common.get_source_directory('chromium')
    self.build_directory = self.build_dir_name()
    return self.build_directory


class GenericBuilder(BinaryProvider):
  """Provides a base for binary builders."""

  def __init__(self, testcase, definition, binary_name, target, options):
    """self.git_sha must be set in a subclass, or some of these
    instance methods may not work."""
    super(GenericBuilder, self).__init__(
        testcase_id=testcase.id,
        build_url=testcase.build_url,
        binary_name=binary_name)
    self.testcase = testcase
    self.target = target if target else binary_name
    self.options = options
    self.source_directory = os.environ.get(definition.source_var)
    self.gn_args = None
    self.gn_args_options = {}
    self.gn_flags = '--check'
    self.definition = definition

  def out_dir_name(self):
    """Returns the correct out dir in which to build the revision.
      Directory name is of the format clusterfuzz_<testcase_id>_<git_sha>."""

    dir_name = os.path.join(
        self.source_directory, 'out',
        'clusterfuzz_%s' % self.options.testcase_id)
    return dir_name

  def checkout_source_by_sha(self):
    """Checks out the correct revision."""
    if get_current_sha(self.source_directory) == self.git_sha:
      logger.info(
          'The current state of %s is already on the revision %s (commit=%s). '
          'No action needed.', self.source_directory, self.testcase.revision,
          self.git_sha)
      return

    binary = 'git'
    args = 'checkout %s' % self.git_sha
    common.check_confirm(CHECKOUT_MESSAGE.format(
        revision=self.testcase.revision,
        cmd='%s %s' % (binary, args),
        source_dir=self.source_directory))

    if is_repo_dirty(self.source_directory):
      raise error.DirtyRepoError(self.source_directory)

    ensure_sha(self.git_sha, self.source_directory)
    common.execute(binary, args, self.source_directory)

  def deserialize_gn_args(self, args):
    """Convert gn args into a dict."""

    args_hash = {}
    for line in args.splitlines():
      key, val = line.split('=')
      args_hash[key.strip()] = val.strip()
    return args_hash

  def serialize_gn_args(self, args_hash):
    args = []
    for key, val in sorted(args_hash.iteritems()):
      args.append('%s = %s' % (key, val))
    return '\n'.join(args)

  def setup_gn_goma_params(self, gn_args):
    """Ensures that goma_dir and gn_goma are used correctly."""
    if not self.options.goma_dir:
      self.options.goma_dir = False
      gn_args.pop('goma_dir', None)
      gn_args['use_goma'] = 'false'
    else:
      gn_args['use_goma'] = 'true'
      gn_args['goma_dir'] = '"%s"' % self.options.goma_dir
    return gn_args

  def setup_gn_args(self):
    """Ensures that args.gn is set up properly."""
    gn_args = read_gn_args(
        self.gn_args,
        downloaded_args_gn_path=os.path.join(
            self.build_dir_name(), ARGS_GN_FILENAME))

    # Add additional options to existing gn args.
    args_hash = self.deserialize_gn_args(gn_args)
    for k, v in self.gn_args_options.iteritems():
      args_hash[k] = v

    args_hash = self.setup_gn_goma_params(args_hash)
    args_hash = setup_debug_symbol_if_needed(
        args_hash, self.definition.sanitizer, self.options.enable_debug)

    self.gn_args = args_hash

  def gn_gen(self):
    """Finalize args.gn and run `gn gen`."""
    args_gn_path = os.path.join(self.build_directory, ARGS_GN_FILENAME)

    common.ensure_dir(self.build_directory)
    common.delete_if_exists(args_gn_path)

    # Let users edit the current args.
    content = self.serialize_gn_args(self.gn_args)
    content = common.edit_if_needed(
        content, prefix='edit-args-gn-',
        comment='Edit %s before building.' % ARGS_GN_FILENAME,
        should_edit=self.options.edit_mode)

    # Write args to file and store.
    with open(args_gn_path, 'w') as f:
      f.write(content)
    self.gn_args = self.deserialize_gn_args(content)

    logger.info(
        common.colorize('\nGenerating %s:\n%s\n', common.BASH_GREEN_MARKER),
        args_gn_path, content)

    common.execute('gn', 'gen %s %s' % (self.gn_flags, self.build_directory),
                   self.source_directory)

  def install_deps(self):
    """Run all commands that only need to run once. This means the commands
      within this method are not required to be executed in a subsequential
      run."""
    pass

  def gclient_sync(self):
    """Run gclient sync. This is separated from install_deps because it is
      needed in every build."""
    common.execute('gclient', 'sync', self.source_directory)

  def gclient_runhooks(self):
    """Run gclient runhooks. This is separated from install_deps because it is
      needed in every build, yet the arguments might differ."""
    pass

  def setup_all_deps(self):
    """Setup all dependencies."""
    if self.options.skip_deps:
      return
    self.gclient_sync()
    self.gclient_runhooks()
    self.install_deps()

  def get_goma_cores(self):
    """Choose the correct amount of GOMA cores for a build."""
    if self.options.goma_threads:
      return self.options.goma_threads
    else:
      cpu_count = multiprocessing.cpu_count()
      return 50 * cpu_count if self.options.goma_dir else (3 * cpu_count) / 4

  def get_goma_load(self):
    """Choose the correct amount of GOMA load for a build."""
    if self.options.goma_load:
      return self.options.goma_load
    return multiprocessing.cpu_count() * 2

  def build_target(self):
    """Build the correct revision in the source directory."""
    self.setup_gn_args()
    self.setup_all_deps()
    self.gn_gen()

    common.execute(
        'ninja',
        ("-w 'dupbuild=err' -C {build_dir} -j {goma_cores} -l {goma_load} "
         '{target}'.format(
             build_dir=self.build_directory,
             goma_cores=self.get_goma_cores(),
             goma_load=self.get_goma_load(),
             target=self.target)),
        self.source_directory,
        capture_output=False,
        stdout_transformer=output_transformer.Ninja())

  def get_build_directory(self):
    """Returns the location of the correct build to use for reproduction."""

    if self.build_directory:
      return self.build_directory

    if not self.gn_args:
      self.download_build_data()

    self.build_directory = self.build_dir_name()

    if not self.source_directory:
      self.source_directory = common.get_source_directory(self.name)

    if not self.options.current:
      self.checkout_source_by_sha()

    self.build_directory = self.out_dir_name()
    self.build_target()

    return self.build_directory


class PdfiumBuilder(GenericBuilder):
  """Build a fresh Pdfium binary."""

  def __init__(self, testcase, definition, options):
    super(PdfiumBuilder, self).__init__(
        testcase=testcase,
        definition=definition,
        binary_name='pdfium_test',
        target=None,
        options=options)
    self.chromium_sha = sha_from_revision(testcase.revision, 'chromium/src')
    self.name = 'Pdfium'
    self.git_sha = get_pdfium_sha(self.chromium_sha)
    self.gn_args = testcase.gn_args
    self.gn_args_options = {'pdf_is_standalone': 'true'}
    self.gn_flags = ''


class ChromiumBuilder(GenericBuilder):
  """Builds a specific target from inside a Chromium source repository."""

  def __init__(self, testcase, definition, options):
    target_name = None
    binary_name = definition.binary_name
    if definition.target:
      target_name = definition.target
    if not binary_name:
      binary_name = common.get_binary_name(testcase.stacktrace_lines)

    super(ChromiumBuilder, self).__init__(
        testcase=testcase,
        definition=definition,
        binary_name=binary_name,
        target=target_name,
        options=options)
    self.git_sha = sha_from_revision(self.testcase.revision, 'chromium/src')
    self.gn_args = testcase.gn_args
    self.name = 'chromium'

  def install_deps(self):
    """Run all commands that only need to run once. This means the commands
      within this method are not required to be executed in a subsequential
      run."""
    common.execute('python', 'tools/clang/scripts/update.py',
                   self.source_directory)

  def gclient_runhooks(self):
    """Run gclient runhooks. This is separated from install_deps because it is
      needed in every build, yet the arguments might differ."""
    common.execute('gclient', 'runhooks', self.source_directory)


class V8Builder(GenericBuilder):
  """Builds a fresh v8 binary."""

  def __init__(self, testcase, definition, options):
    super(V8Builder, self).__init__(
        testcase=testcase,
        definition=definition,
        binary_name='d8',
        target=None,
        options=options)
    self.git_sha = sha_from_revision(testcase.revision, 'v8/v8')
    self.gn_args = testcase.gn_args
    self.name = 'V8'

  def install_deps(self):
    """Run all commands that only need to run once. This means the commands
      within this method are not required to be executed in a subsequential
      run."""
    common.execute('python', 'tools/clang/scripts/update.py',
                   self.source_directory)

  def gclient_runhooks(self):
    """Run gclient runhooks. This is separated from install_deps because it is
      needed in every build, yet the arguments might differ."""
    common.execute('gclient', 'runhooks', self.source_directory)


class CfiChromiumBuilder(ChromiumBuilder):
  """Build a CFI chromium build."""

  def install_deps(self):
    """Run download_gold_plugin.py."""
    super(CfiChromiumBuilder, self).install_deps()
    common.execute('build/download_gold_plugin.py', '', self.source_directory)


class MsanChromiumBuilder(ChromiumBuilder):
  """Build a MSAN chromium build."""

  def gclient_runhooks(self):
    """Run gclient runhooks."""
    gclient_runhooks_msan(
        self.source_directory, self.gn_args.get('msan_track_origins'))


class MsanV8Builder(V8Builder):
  """Build a MSAN V8 build."""

  def gclient_runhooks(self):
    """Run gclient runhooks."""
    gclient_runhooks_msan(
        self.source_directory, self.gn_args.get('msan_track_origins'))


class ChromiumBuilder32Bit(ChromiumBuilder):
  """Build a 32-bit chromium build."""

  def install_deps(self):
    """Install other deps."""
    super(ChromiumBuilder32Bit, self).install_deps()
    install_build_deps_32bit(self.source_directory)


class V8Builder32Bit(V8Builder):
  """Build a 32-bit V8 build."""

  def install_deps(self):
    """Install other deps."""
    super(V8Builder32Bit, self).install_deps()
    install_build_deps_32bit(self.source_directory)
