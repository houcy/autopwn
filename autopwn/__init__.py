#!/usr/bin/env python3

import argparse
import copy
import cmd
import operator
import os
import random
import re
import shlex
import subprocess
import sys
import threading
import time

from collections import OrderedDict, defaultdict
from distutils.spawn import find_executable
from locale import getlocale
from subprocess import Popen, PIPE
from time import gmtime, strftime

import inquirer
from screenutils import list_screens, Screen
import yaml

# Aidan Marlin @ NCC Group
# Project born 201502
# Project reborn 201505

class Arguments:
    argparse_description = '''
autopwn v0.17.0
By Aidan Marlin
Email: aidan [dot] marlin [at] nccgroup [dot] com'''

    argparse_epilog = '''
Format of the target file should be:

targets:
    - name: <target-name>
      ip_address: <ip-address>
      domain: <domain>
      url: <url-path>
      port: <port-number>
      protocol: <protocol>
      mac_address: <mac_address>
    - name: <target-name-1>
      ip_address_list: <ip-address-list>
      ...

Only 'name' and 'ip_address' are compulsory options.
Example file:

targets:
    - name: test
      ip_address: 127.0.0.1
      domain: test.com
      url: /test
      port: 80
      protocol: https
      mac_address: ff:ff:ff:ff:ff:ff
      cookies:
        some-cookie-name: some-cookie-value
        some-cookie-name1: some-cookie-value1
    - name: test-1
      ip_address_list: ip_list.txt
      cookies_file: cookies.txt

autopwn uses the tools/ directory located where this
script is to load tool definitions, which are yaml
files. You can find some examples in the directory
already. If you think one is missing, mention it on
GitHub or email me and I might add it.

autopwn also uses assessments/ for assessment definitions.
Instead of selecting which tools you would like to run,
you specify which assessment you would like to run.
Assessment configuration files contain lists of tools
which will be run as a result.

Have fun!
Legal purposes only..
'''

    def __init__(self, argslist):
        self.parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                         description=self.argparse_description,
                                         epilog=self.argparse_epilog)
        self.parser.add_argument('-t', '--target',
                                 required=True,
                                 help='The file containing the targets')
        self.parser.add_argument('-m', '--module',
                                 help='Specify module (tool or assessment) to run. Autopwn will not '
                                 'drop to shell if this option is specified',
                                 required=True)
        self.parser.add_argument('-d', '--assessment_directory',
                                 help='Specify assessment directory')
        self.parser.add_argument('-s', '--with_screen',
                                 action='store_true',
                                 help='Run tools in screen session')
        self.parser.add_argument('-p', '--parallel',
                                 action='store_true',
                                 help='Run tools in parallel regardless of assessment or '
                                 'global parallel option')

        self.parser = self.parser.parse_args(argslist)

        # TODO check file exists
        stream = open(self.parser.target, 'r')
        target_objects = yaml.load(stream)

        config = Configuration()
        config.arguments['command_line'] = True

        # Process boolean command line arguments
        if self.parser.with_screen == True:
            config.arguments['screen'] = True
        if self.parser.parallel == True:
            config.arguments['parallel'] = True

        for target in target_objects['targets']:
            Use(config,self.parser.module)
            AutoSet(config,target,self.parser.module)
            Save(config)
            View('command_line_save',config,target=target)

            # Reset for next target
            config.instance = {}
            config.instance['tool'] = []
            config.instance['config'] = {}
        Show(config,'jobs')

class Configuration:
    def __init__(self):
        self.log_started = False
        self.resource_found = False

        self.global_config = {}
        self.command_line = False
        self.tools = []
        self.assessments = []
        self.job_queue = []

        self.arguments = ddict_options = defaultdict(lambda : '')

        self.instance = {}
        self.instance['tool'] = []
        self.instance['config'] = {}

        self.load("tools")
        self.load("assessments")
        self.load("global_config")

    def find_path(self, candidate):
         basepath = os.path.dirname(candidate)
         tools_dir = os.path.join(basepath, 'tools')
         if os.path.exists(tools_dir):
             return basepath

    def load(self, load_type):
        pathname = os.path.abspath(self.find_path(__file__) or find_path(sys.argv[0]))

        if load_type == "tools":
            load_directory = os.path.abspath(pathname) + "/tools/"
            load_string = "Tools"
        elif load_type == "assessments":
            load_directory = os.path.abspath(pathname) + "/assessments/"
            load_string = "Assessments"
        elif load_type == "global_config":
            load_directory = os.path.abspath(pathname) + "/"
            load_string = "Global configuration"

        if not os.path.isdir(load_directory):
            Error(10,"[E] " + load_string + "directory does not exist")

        for file in os.listdir(load_directory):
            if file.endswith(".apc"):
                stream = open(load_directory + file, 'r')
                objects = yaml.load(stream)
                # TODO Make this better
                if load_type == "tools":
                    self.tools.append(objects)
                elif load_type == "assessments":
                    self.assessments.append(objects)
                elif load_type == "global_config":
                    self.global_config = objects

class Error:
    def __init__(self, error_code, error_message):
        print(error_message)
        sys.exit(error_code)

class Search:
    def __init__(self, config, search_string):
        self.search(config.assessments,"Assessment","assessment/",search_string)
        self.search(config.tools,"Tool","tool/",search_string)

    def search(self,config_item,item_type_string,item_type_prepend,search_string):
        print('{0:30} {1}'.format(item_type_string, "Description"))
        print('-'*40)
        print()
        for item in config_item:
            if search_string in item['name'] \
               or str.lower(search_string) in str.lower(item['description']):
                name = item_type_prepend + item['name']
                description = item['description']
                if (sys.stdout.isatty()) == True:
                    description = '\x1b[%sm%s\x1b[0m' % \
                        (';'.join(['32']), description)
                print('{0:30} {1}'.format(name, description))
        print()

class Use:
    def __init__(self, config, arg):
        config.instance = {}
        config.instance['tool'] = []
        config.instance['config'] = {}

        resource = arg.split('/')
        if resource[0] == 'tool':
            self.use_tool(config,resource[1])
        elif resource[0] == 'assessment':
            self.use_assessment(config,resource[1])
        else:
            config.resource_found = False
            return

    def use_tool(self, config, tool_name):
        config.resource_found = False

        for tool in config.tools:
            if tool['name'] == tool_name:
                config.resource_found = True
                config.instance['tool'].append(tool['name'])

        if config.resource_found == False:
            print("Tool not found")
            return

        print()

    def use_assessment(self, config, assessment_name):
        for assessment in config.assessments:
            if assessment['name'] == assessment_name:
                config.resource_found = True
                if config.command_line != True:
                    print('Name: ' + assessment['name'])
                # Find all tools with assessment type
                for tool in config.tools:
                    for assessment_type in tool['assessment_groups']:
                        if assessment_type == assessment_name:
                            config.instance['tool'].append(tool['name'])
                            if config.command_line != True:
                                print("    - " + tool['name'])
        if config.command_line != True:
          print()

class Show:
    def __init__(self, config, arg):
        if arg == 'options':
            self.show_options(config)
        elif arg == 'jobs':
            self.show_jobs(config)
        elif arg == 'config':
            self.show_config(config)
        else:
            self.show_help(config)

    def show_help(self,config):
        info = '''
Valid arguments for show are:
    options    - Show options for tool or assessment
    jobs       - Show jobs
    config     - Show autopwn config
'''
        print(info)
        return True

    def show_config(self,config):
        print()
        print("        {0:30} {1}".format("Option", "Value"))
        print("        "+"-"*40)
        for option in config.global_config:
            print("        {0:30} {1}".format(option, config.global_config[option]))
        print()

    def show_jobs(self,config):
        if len(config.job_queue) == 1:
            print("There is 1 job in the queue")
        else:
            print("There are " + str(len(config.job_queue)) + " jobs in the queue")
        print()

    def show_options(self,config):
        if len(config.instance['tool']) == 0:
            print("You need to select a tool or assessment first.")
            return False
        # Determine what options are needed for tool(s)
        print("Options for tool/assessment.")
        print()
        print("        {0:30} {1}".format("Option", "Value"))
        print("        "+"-"*40)
        option_displayed = []
        for tool in config.tools:
            if tool['name'] in config.instance['tool']:
                for required_arg in tool['rules']['target-parameter-exists']:
                    # Don't print options more than once
                    if required_arg in option_displayed:
                        continue
                    else:
                        option_displayed.append(required_arg)
                        if type(required_arg) is list:
                            for arg in required_arg:
                                try:
                                    print("        {0:30} {1}".format(arg,config.instance['config'][arg]))
                                except:
                                    print("        {0:30}".format(arg))
                        else:
                            try:
                                print("        {0:30} {1}".format(required_arg,\
                                    config.instance['config'][required_arg]))
                            except:
                                print("        {0:30}".format(required_arg))
        print()

class Unset:
    def __init__(self, config, arg):
        context = ''
        args = arg.split(" ")

        # Check number of arguments specified
        if len(args) != 1:
            print("Wrong number of arguments specified for set")
            return
        option = args[0]

        # If global.some_option set, switch context
        option_with_context = option.split('.')
        if len(option_with_context) == 2:
            context = option_with_context[0]
            option = option_with_context[1]

        # If context is 'global', set in global config file and load?
        if context == 'global':
            config.global_config[option] = ''
            # TODO check file exists etc
            with open('autopwn.apc', 'w') as global_config_file:
                global_config_file.write( yaml.dump(config.global_config, default_flow_style=True) )
            config.load("global_config")
        else:
            config.instance['config'][option] = ''

        print(option + " = " + "''")

# When a target file is specified
class AutoSet:
    def __init__(self, config, target_objects, selected_module):
        for option in target_objects:
            config.instance['config'][option] = target_objects[option]

class Set:
    def __init__(self, config, arg):
        context = ''
        args = arg.split(" ")

        # Check number of arguments specified
        if len(args) != 2:
            print("Wrong number of arguments specified for set")
            return
        option = args[0]
        value = args[1]

        # If global.some_option set, switch context
        option_with_context = option.split('.')
        if len(option_with_context) == 2:
            context = option_with_context[0]
            option = option_with_context[1]

        # Boolean conversions
        if value.lower() == 'true':
            value = True
        elif value.lower() == 'false':
            value = False

        # If context is 'global', set in global config file and load?
        if context == 'global':
            config.global_config[option] = value
            # TODO check file exists etc
            with open('autopwn.apc', 'w') as global_config_file:
                global_config_file.write( yaml.dump(config.global_config, default_flow_style=True) )
            config.load("global_config")
        else:
            config.instance['config'][option] = value

        print(option + " = " + str(value))

class Process:
    def __init__(self, config):
        info = {}

        if len(config.job_queue) == 0:
            print("No jobs to run")
            return

        for instance in config.job_queue:
            instance['parallel'] = False
            instance['options']['date'] = strftime("%Y%m%d_%H%M%S%z")
            instance['options']['date_day'] = strftime("%Y%m%d")
            instance['options']['output_dir'] = instance['options']['date_day'] + \
                                "_autopwn_" + \
                                instance['options']['target_name'] + \
                                "_" + instance['options']['target']

            if self.binary_exists(instance['binary_name']) != True:
                Error(50,"[E] Missing binary/script - " + instance['binary_name'])

            instance['execute_string'] = instance['binary_name'] + " " + instance['arguments']

            if config.arguments['screen'] == True:
                if binary_exists('screen') != True and binary_exists('bash') != True:
                    Error(50,"[E] Missing binary")
                instance['execute_string'] = "screen -D -m -S autopwn_" + \
                        instance['target_name'] + "_" + \
                        instance['target'] + "_" + \
                        instance['tool'] + " " + "bash -c '" + \
                        instance['execute_string'] + \
                        "'"
            ddict_options = defaultdict(lambda : '')
            for option in instance['options']:
                ddict_options[option] = instance['options'][option]

            # Option replacements
            instance['execute_string'] = instance['execute_string'].format(**ddict_options)

        # Run jobs
        Execute(config)

    def binary_exists(self, binary_string):
        try:
            which_return_code = subprocess.call(["which",binary_string],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if which_return_code == 0:
                return True
            else:
                return False
        except OSError as e:
            if e.errno == os.errno.ENOENT:
                Error(55,"[E] 'which' binary couldn't be found")
            else:
                # Not sure what's happening at this point
                raise

class Save:
    def __init__(self, config):
        if len(config.instance['tool']) == 0:
            print("No tool options to save")
            return
        for imported_tool in config.tools:
            for selected_tool in config.instance['tool']:
                if selected_tool == imported_tool['name']:
                    config.job_queue.append(copy.deepcopy(imported_tool))
                    config.job_queue[-1]['options'] = {}
                    for option in config.instance['config']:
                        config.job_queue[-1]['options'][option] = config.instance['config'][option]

                    # Check all required parameters exist before save
                    for parameter in imported_tool['rules']['target-parameter-exists']:
                        parameter_found = False
                        if type(parameter) is list:
                            for arg in parameter:
                                if arg in config.job_queue[-1]['options']:
                                    parameter_found = parameter_found or True
                        else:
                            if parameter in config.job_queue[-1]['options']:
                                parameter_found = True

                        if parameter_found == False:
                            # TODO Check this actually works now assessments are in
                            config.job_queue.pop()
                            print("Some required parameters have not been set")
                            return

        if config.command_line != True:
            if len(config.job_queue) == 1:
                print("There is 1 job in the queue")
            else:
                print("There are " + str(len(config.job_queue)) + " jobs in the queue")

class Execute:
    thread = []
    index = 0

    def __init__(self, config):
        for instance in config.job_queue:
            print(instance['execute_string'])

            # Create log directory in CWD
            if not os.path.exists(instance['options']['output_dir']):
                try:
                    os.makedirs(instance['options']['output_dir'])
                except OSError as e:
                    Error(20,"[E] Error creating output directory: " + e)

            if 'url' in instance:
                log = Log(config, os.getcwd(), False, 'tool_string',"# Executing " + \
                          instance['name'] + " tool (" + instance['url'] + "):\n" + \
                          instance['execute_string'])
            else:
                log = Log(config, os.getcwd(), False, 'tool_string',"# Executing " + \
                          instance['name'] + " tool:\n# " + \
                          instance['execute_string'])

            time.sleep (0.1);
            self.thread.append(RunThreads(config,instance))
            # If main process dies, everything else *SHOULD* as well
            self.thread[-1].daemon = True
            # Start threads
            self.thread[-1].start()

            # Parallel or singular?
            if instance['parallel'] != True:
                while threading.activeCount()>1:
                    pass

                self.index = self.index + 1
            else:
                print(instance['execute_string'])
                pass

        if instance['parallel'] == True:
            while threading.activeCount()>1:
                pass
            #for tid in self.thread:
            #    tid.join(1)

class RunThreads (threading.Thread):
    def __init__(self, config, instance):
        threading.Thread.__init__(self)
        self.tool_stdout = ''
        self.tool_sterr = ''
        self.instance = instance
        self.config = config

    def execute_tool(self):
        # Always check any tools provided by
        # community members
        # Bad bug using this and no shell for Popen,
        # will come back to this
        #command_arguments = shlex.split(tool_execute_string)
        proc = Popen(self.instance['execute_string'], stdout=PIPE, stderr=PIPE, shell=True)

        decode_locale = lambda s: s.decode(getlocale()[1])
        self.tool_stdout, self.tool_stderr = map(decode_locale, proc.communicate())

        exitcode = proc.returncode

    def run(self):
        print("[+] Launching " + self.instance['name'])
        self.execute_tool()
        print("[-] " + self.instance['name'] + " is done..")
        # Should we create a stdout log for this tool?
        stdout_boolean = self.instance['stdout']
        if stdout_boolean == True:
            log = Log(self.config, os.getcwd() + "/" + self.instance['options']['output_dir'],
                      self.instance['options']['target_name'] + "_" + self.instance['name'],
                      'tool_output', self.tool_stdout)
        log = Log(self.config, os.getcwd(), False, 'tool_string', "# " + \
                  self.instance['name'] + " has finished")

class Log:
    def __init__(self, config, directory, log_filename, log_type, log_string):
        date = strftime("%Y%m%d")
        date_time = strftime("%Y%m%d %H:%M:%S %z")

        if log_type == 'tool_output':
            try:
                # log_filename is pikey, make it better
                log_file = open(directory + "/" + date + "_autopwn_" + \
                                log_filename + "_stdout.log","a")
            except OSError as e:
                Error(30,"[E] Error creating log file: " + e)

            log_file.write(log_string)
            log_file.close()

        if log_type == 'tool_string':
            try:
                log_file = open(date + "_autopwn_commands.log","a")
            except OSError as e:
                Error(30,"[E] Error creating log file: " + e)
            if config.log_started != True:
                log_file.write("## autopwn v0.17.0 command output\n")
                log_file.write("## Started logging at " + date_time + "...\n")
                config.log_started = True

            log_file.write("# " + date_time + "\n")
            log_file.write(log_string + "\n")
            log_file.close()

        if log_type == 'individual_target':
            try:
                log_file = open(directory + "/target","w")
            except OSError as e:
                Error(30,"[E] Error creating log file: " + e)
            log_file.write(log_string + "\n")
            log_file.close()

class Run:
    def __init__(self, config, arg):
        # Process job queue (replace placeholders)
        Process(config)

class Debug:
    def __init__(self, config, arg):
        for item in config.tools:
            print(item)

class Clear:
    def __init__(self, config, arg):
        config.job_queue = []
        print("Job queue cleared")

class View:
    def __init__(self, view, config, **kwargs):
        if kwargs is None:
            kwargs = defaultdict(lambda : '')

        if view == 'clear':
            pass
        if view == 'command_line_save':
            for key, value in kwargs.items():
                print("Loading " + value['target_name'] + "...")
                print("Done!")
        if view == 'use':
            if config.resource_found == False:
                print("Please specify a valid tool or assessment")
            else:
                for key, value in kwargs.items():
                    print('Name: ' + value['name'])
                    print('Description: ' + value['description'])
                    print('URL: ' + value['url'])
                    print()
                    print("Required options:")
                    for required_arg in value['rules']['target-parameter-exists']:
                        if type(required_arg) is list:
                            if config.command_line != True:
                                print("    One of the following:")
                                for arg in required_arg:
                                    print("        - " + arg)
                        else:
                            if config.command_line != True:
                                print("    - " + required_arg)

class CleanUp():
    def __init__(self):
        pass

class Shell(cmd.Cmd):
    config = Configuration()

    intro = 'autopwn v0.17.0 shell. Type help or ? to list commands.\n'
    prompt = 'autopwn > '

    def do_clear(self, arg):
        'Clear job queue'
        Clear(self.config,arg)
        View('clear',self.config)

    def do_search(self, arg):
        'Search function'
        Search(self.config,arg)
        View('search',self.config)

    def do_debug(self, arg):
        'Show debug information'
        Debug(self.config,arg)
        View('debug',self.config)

    def do_show(self, arg):
        'Show information'
        Show(self.config,arg)
        View('show',self.config)

    def do_save(self, arg):
        'Save instance settings'
        Save(self.config)
        View('save',self.config)

    def do_run(self, arg):
        'Run job queue'
        Run(self.config,arg)
        View('run',self.config)

    def do_use(self, arg):
        'Setup a tool or assessment'
        Use(self.config,arg)
        View('use',self.config)
        if self.config.resource_found == True:
            self.prompt = 'autopwn (' + arg + ') > '

    def do_set(self, arg):
        'Set configuration option'
        Set(self.config,arg)
        View('set',self.config)

    def do_unset(self, arg):
        'Clear configuration option'
        Unset(self.config,arg)
        View('unset',self.config)

    def do_bye(self, arg):
        'Quit autopwn'
        self.terminate()

    def do_exit(self, arg):
        'Quit autopwn'
        self.terminate()

    def do_quit(self, arg):
        'Quit autopwn'
        self.terminate()

    def terminate(self):
        'Exit Autopwn'
        quote = []
        quote.append("Never underestimate the determination of a kid who is time-rich and cash-poor.")
        quote.append("There are few sources of energy so powerful as a procrastinating college student.")
        quote.append("I/O, I/O, It's off to disk I go. A bit or byte to read or write, I/O, I/O, I/O...")
        quote.append("SUPERCOMPUTER: what it sounded like before you bought it.")
        quote.append("Is reading in the bathroom considered Multi-Tasking?")
        quote.append("Premature optimisation is the root of all evil.")
        quote.append("The first rule of optimisation is: Don't do it. The second rule of optimisation is: Don't do it yet.")
        quote.append("Q: How many software engineers does it take to change a lightbulb? A: It can't be done; it's a hardware problem.")
        quote.append("Hackers are not crackers.")
        quote.append("Behind every successful Coder there an even more successful De-coder ")
        quote.append("If at first you don't succeed; call it version 1.0.")
        quote.append("F*ck it, we'll do it in production.")
        quote.append("Programmers are tools for converting caffeine into code.")
        quote.append("Those who can't write programs, write help files.")
        print(random.choice(quote))
        sys.exit(0)

def _main(arglist):
    # Process command line arguments
    if len(sys.argv) > 1:
        Arguments(sys.argv[1:]).parser
    else:
        # Drop user to shell
        Shell().cmdloop()

def main():
    try:
        _main(sys.argv[1:])
    except KeyboardInterrupt:
        CleanUp()
        print()
        print("[E] Quitting!")
        sys.exit(1)

if __name__ == "__main__":
    main()
