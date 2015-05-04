#!/usr/bin/env python3
# import tarfile
# import magic
import json
import os
from clint.textui import progress
import requests
import subprocess
import argparse
import tempfile
import shutil
import collections
import sys
import glob

RULE_DIR = '/etc/foopkg/rules.d'
BUILD_DIR_BASE = '/var/build'
rules = {}
dryrun = False
redownload = False
debug = True

class ColorCodes(object):
	# https://gist.github.com/martin-ueding/4007035
	def __init__(self):
		try:
			self.bold = subprocess.check_output("tput bold".split())
			self.reset = subprocess.check_output("tput sgr0".split())
 
			self.blue = subprocess.check_output("tput setaf 4".split())
			self.green = subprocess.check_output("tput setaf 2".split())
			self.orange = subprocess.check_output("tput setaf 3".split())
			self.red = subprocess.check_output("tput setaf 1".split())
		except subprocess.CalledProcessError as e:
			self.bold = ""
			self.reset = ""
 
			self.blue = ""
			self.green = ""
			self.orange = ""
			self.red = ""
colours = ColorCodes()

def gprint(line):
	print(line)
	# Currently bugged. For some reason that escapes us all, this dumps characters all over the line instead of changing its colour.
	# print(colours.green, colours.bold, line, colours.reset)

def dprint(line):
	if debug: print('DEBUG: ', line)

# tarfile, meet crude wrapper. He's your new replacement.
def untar(f, out, strip_components=0):
	shutil.rmtree(out, ignore_errors=True)
	os.mkdir(out)
	subprocess.check_call(['/bin/tar', '-x', '--file='+f, '--strip-components='+str(strip_components), '--directory='+out], stderr=sys.stdout)

def my_check_call(command, logfile):
	try:
		subprocess.check_call(command, stdout=logfile, stderr=logfile)
	except subprocess.CalledProcessError:
		print('ERROR: Nonzero exit code returned by `{}`! Check the relevant build.log.'.format(command[0]))
		logfile.close()
		exit(1)

def get_confirmation(message, default=None):
	confirm = '[y/n]'
	if default == True:
		confirm = '[Y/n]'
	elif default == False:
		confirm = '[y/N]'
	while True:
		a = input(message + confirm).lower().strip()
		if a[0] == 'y' or (default == True and a == ''):
			return True
		elif a[0] == 'n' or (default == False and a == ''):
			cont = False
			exit(0)
		else:
			print('Answer not valid.')

def check_installed(package):
	latest = '{}-{}'.format(package, rules[package]['version'])
	matchinginstalled = is_installed(package, get_matching=True)
	if latest in matchinginstalled:
		print('Package already installed! If you want to reinstall, pass --reinstall.')
		exit(0)
	elif matchinginstalled != []:
		c = get_confirmation('Matching package{} {} installed. Do you want to continue installation? [y/N]'
							.format('s' if len(matchinginstalled) else '', ', '.join(matchinginstalled)))
		if not c:
			exit(0)
	return

def is_installed(package, get_matching=False):
	latest = '{}-{}'.format(package, rules[package]['version'])
	matchinginstalled = glob.glob('/var/log/porg/{}-[0-9]*'.format(package))
	return bool(matchinginstalled) if not get_matching else matchinginstalled

def compile_item(name, item, builddir, untardir):
	special = 'build' in item
	# If there are no special conditions, do stuff normally.
	os.chdir(untardir)
	with open(os.path.join(untardir, 'build.log'), 'w') as buildlog:
		gprint('-> This build is being logged to {}'.format(os.path.join(untardir, 'build.log')))
		gprint('-> Configuring package {}'.format(name))
		makedir = '' if special and 'outside-source-dir' in item['build'] else None
		if special and 'outside-source-dir' in item['build']:
			makedir = tempfile.mkdtemp()
			gprint('--> Handling package {} in temporary directory {}'.format(name, makedir))
			os.chdir(makedir)
		configureargs = [os.path.join(untardir, 'configure')]
		if special and 'configure-args' in item['build']: configureargs.extend(item['build']['configure-args'])
		my_check_call(configureargs, buildlog)

		gprint('-> Compiling package {}, go get some tea'.format(name))
		makeargs = [(special and 'make-binary' in item['build']) or '/usr/bin/make']
		if special and 'make-args' in item['build']: makeargs.extend(item['build']['make-args'])
		my_check_call(makeargs, buildlog)
		
		if not dryrun or (special and 'no-make-install' in item['build']):
			gprint('-> Installing package {}'.format(name))
			my_check_call(['/usr/local/bin/porg', '-lp', '{}-{}'.format(name, item['version']), '/usr/bin/make install'], buildlog)
		if makedir:
			gprint('-> Deleting temporary directory')
			shutil.rmtree(makedir)

def load_rules():
	global rules
	rulesfiles = os.listdir(RULE_DIR)
	rulesfiles = [os.path.join(RULE_DIR, f) for f in rulesfiles]
	for f in rulesfiles:
		with open(f, 'r') as h:
			j = h.read()
			rules.update(json.loads(j))
			dprint('Loaded rules from file {}'.format(f))
			h.close()
	# with open(RULE_DIR, 'r') as h:
	#	 rules = json.loads(h.read())
	#	 h.close()

def resolve_deps(package):
	deplist = {}
	if not 'depends' in rules[package]: return [package]
	il = collections.deque()
	il.append(package)

	for p in rules:
		deplist[p] = []
		if 'depends' in rules[p]:
			deplist[p] = rules[p]['depends']

	ws = collections.deque()
	ws.extend(deplist[package])

	while len(ws) > 0:
		p = ws.popleft()
		if p in il:
			il.remove(p)
		il.appendleft(p)
		if deplist[p] != []:
			ws.extend(deplist[p])
	return il

def get_install_list(package):
	installlist = list(resolve_deps(package))
	for pkg in installlist:
		if is_installed(pkg):
			gprint('-> {} already installed, skipping'.format(package))
			installlist.remove(pkg)
	return installlist

def progress_download(url, path):
	# http://stackoverflow.com/a/20943461
	r = requests.get(url, stream=True)
	with open(path, 'wb') as f:
		total_length = int(r.headers.get('content-length'))
		for chunk in progress.bar(r.iter_content(chunk_size=1024), expected_size=(total_length/1024) + 1): 
			if chunk:
				f.write(chunk)
				f.flush()

def install_item(name, item):
	if os.geteuid() != 0: raise PermissionError("You forgot a sudo. If you didn't, this is a bug.")
	builddir = os.path.join(BUILD_DIR_BASE, name)
	filename = os.path.join(builddir, '{}-{}.dl'.format(name, item['version']))
	untardir = os.path.join(builddir, '{}-{}'.format(name, item['version']))
	os.makedirs(builddir, exist_ok=True)
	os.chdir(builddir)
	gprint('-> Downloading package')
	if os.path.isfile(filename) and not redownload:
		gprint("--> File exists, not redownloading. Pass --redownload to force a redownload.")
	else:
		progress_download(item['url'], filename)
	gprint('-> Untarring file')
	untar(filename, untardir, strip_components=1)
	compile_item(name, item, builddir, untardir)

if __name__ == '__main__':
	load_rules()
	# print(rules)
	# running program from shell
	parser = argparse.ArgumentParser(description='Compile/install packages from tar files')
	parser.add_argument('action', metavar='action', help='Action to perform on a package. Can be install, remove, etc.')
	parser.add_argument('package', metavar='pkg', help='Package to perform the action on')
	parser.add_argument('--no-install', '--dry-run', '-d', help="Don't make install", action='store_true')
	parser.add_argument('--redownload', help='Force redownload of already downloaded package', action='store_true')
	parser.add_argument('--file', '-f', metavar='file', help='Custom file or url to be used instead of url in rules.json', type=argparse.FileType(mode='r'))
	parser.add_argument('--version', '-n', metavar='version', help='Custom version to be used as override of version in rules.json')
	args = parser.parse_args()
	dryrun = args.no_install
	redownload = args.redownload
	package = args.package

	if not package in rules:
		rules['package'] = {}
		# TODO: Implement fuzzy matching for package name and action
		print(colours.red, colours.bold, 'ERROR: rules for package {} not defined in {}.'.format(package, RULE_DIR))
		if not (args.file and args.version):
			raise ValueError('Rules not defined and file/version not passed. Cannot continue.')
		# ensure the user wants to do this madness
		if not get_confirmation('Do you want to continue?', default=False):
			exit(0)
	
	rules[package]['version'] = args.version or rules[package]['version']
	rules[package]['url'] = args.file or rules[package]['url']

	if args.action == 'install' or args.action == 'i':
		check_installed(args.package)
		[install_item(i, rules[i]) for i in get_install_list(args.package)]