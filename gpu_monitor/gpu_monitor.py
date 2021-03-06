#!/usr/bin/env python3
"""Script to check the state of GPU servers

This script is most useful in conjunction with an ssh-key, so a password does
not have to be entered for each SSH connection.
"""
import argparse
import logging
import os
import pwd
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from functools import partial
from logging import debug, info, error

# Default timeout in seconds after which SSH stops trying to connect
DEFAULT_SSH_TIMEOUT = 3

# Default timeout in seconds after which remote commands are interrupted
DEFAULT_CMD_TIMEOUT = 10

# Default server file
DEFAULT_SERVER_FILE = 'servers.txt'
SERVER_FILE_PATH = os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])),
                                DEFAULT_SERVER_FILE)

parser = argparse.ArgumentParser(description='Check state of GPU servers')
parser.add_argument('-v', '--verbose', action='store_true',
                    help='Be verbose')
parser.add_argument('-l', '--list', action='store_true', help='Show used GPUs')
parser.add_argument('-f', '--finger', action='store_true',
                    help='Attempt to resolve user names to real names')
parser.add_argument('-m', '--me', action='store_true',
                    help='Show only GPUs used by current user')
parser.add_argument('-U', '--utilization', action='store_true',
                    help='Display GPU utilization')
parser.add_argument('-g', '--graphical', action='store_true',
                    help='Display only graphical processes')
parser.add_argument('-c', '--cuda', action='store_true',
                    help='Display only cuda processes')
parser.add_argument('-u', '--user', help='Shows only GPUs used by a user')
parser.add_argument('-s', '--ssh-user', default=None,
                    help='Username to use to connect with SSH')
parser.add_argument('--ssh-timeout', default=DEFAULT_SSH_TIMEOUT,
                    help='Timeout in seconds after which SSH stops to connect')
parser.add_argument('--cmd-timeout', default=DEFAULT_CMD_TIMEOUT,
                    help=('Timeout in seconds after which nvidia-smi '
                          'is interrupted'))
parser.add_argument('--server-file', default=SERVER_FILE_PATH,
                    help='File with addresses of servers to check')
parser.add_argument('servers', nargs='*', default=[],
                    help='Servers to probe')

# SSH command
SSH_CMD = ('ssh -o "ConnectTimeout={ssh_timeout}" {server} '
           'timeout {cmd_timeout}')

# Command for running nvidia-smi locally
NVIDIASMI_CMD = 'nvidia-smi -q -x'

# Command for running nvidia-smi remotely
REMOTE_NVIDIASMI_CMD = '{} {}'.format(SSH_CMD, NVIDIASMI_CMD)

# Command for running ps locally
PS_CMD = 'ps -o pid= -o ruser= -p {pids}'

# Command for running ps remotely
REMOTE_PS_CMD = '{} {}'.format(SSH_CMD, PS_CMD)

# Command for getting real names remotely
# See https://stackoverflow.com/a/38235661
REAL_NAMES_CMD = """<<-"EOF"
import pwd
for user in [{users}]:
    try:
        print(pwd.getpwnam(user).pw_gecos)
    except KeyError:
        print('Unknown')
EOF
"""
REMOTE_REAL_NAMES_CMD = '{} python - {}'.format(SSH_CMD, REAL_NAMES_CMD)


def format_aligned(data, padding=1):
    """Given a 2d array/list od lists of strings, format them into a
    table with the given right padding per column"""
    text_rows = []
    col_widths = [max(len(word) for word in row) + padding for row in zip(*data)]  # padding
    for row in data:
        text_rows.append("".join(word.ljust(col_widths[ind]) for ind, word in enumerate(row)))
    return "\n".join(text_rows)  # return the padded columns


def run_command(cmd):
    debug('Running command: "{}"'.format(cmd))

    try:
        res = subprocess.check_output(cmd, shell=True)
    except subprocess.TimeoutExpired as e:
        debug(('Command timeouted with output "{}", '
               'and stderr "{}"'.format(e.output.decode('utf-8'), e.stderr)))
        return None
    except subprocess.CalledProcessError as e:
        debug(('Command failed with exit code {}, output "{}", '
               'and stderr "{}"'.format(e.returncode,
                                        e.output.decode('utf-8'),
                                        e.stderr)))
        return None

    return res


def run_nvidiasmi_local():
    res = run_command(NVIDIASMI_CMD)
    return ET.fromstring(res) if res is not None else None


def run_nvidiasmi_remote(server, ssh_timeout, cmd_timeout):
    cmd = REMOTE_NVIDIASMI_CMD.format(server=server,
                                      ssh_timeout=ssh_timeout,
                                      cmd_timeout=cmd_timeout)
    res = run_command(cmd)
    return ET.fromstring(res) if res is not None else None


def run_ps_local(pids):
    cmd = PS_CMD.format(pids=','.join(pids))
    res = run_command(cmd)
    return res.decode('ascii') if res is not None else None


def run_ps_remote(server, pids, ssh_timeout, cmd_timeout):
    cmd = REMOTE_PS_CMD.format(server=server,
                               pids=','.join(pids),
                               ssh_timeout=ssh_timeout,
                               cmd_timeout=cmd_timeout)
    res = run_command(cmd)
    return res.decode('ascii') if res is not None else None


def get_real_names_local(users):
    real_names_by_users = {}
    for user in users:
        try:
            real_names_by_users[user] = pwd.getpwnam(user).pw_gecos
        except KeyError:
            pass
    return defaultdict(lambda: 'Unknown', real_names_by_users)


def get_real_names_remote(server, users, ssh_timeout, cmd_timeout):
    users_str = ','.join(('\'{}\''.format(user) for user in users))
    cmd = REMOTE_REAL_NAMES_CMD.format(server=server,
                                       users=users_str,
                                       ssh_timeout=ssh_timeout,
                                       cmd_timeout=cmd_timeout)
    res = run_command(cmd)
    if res is not None:
        res = res.decode('utf-8')
        real_names_by_users = {user: s.strip()
                               for user, s in zip(users, res.split('\n'))}
        return defaultdict(lambda: 'Unknown', real_names_by_users)
    else:
        return None


def get_users_by_pid(ps_output):
    users_by_pid = {}
    for line in ps_output.strip().split('\n'):
        pid, user = line.split()
        users_by_pid[pid] = user

    return users_by_pid


def get_gpu_infos(nvidiasmi_output):
    """Given the XML output of nvidia-smi, return parsed information on a per-gpu basis"""
    gpus = nvidiasmi_output.findall('gpu')

    gpu_infos = []
    for idx, gpu in enumerate(gpus):
        model = gpu.find('product_name').text
        total_memory = int(gpu.find('fb_memory_usage/total').text.split(' ')[0])
        total_used_memory = int(gpu.find('fb_memory_usage/used').text.split(' ')[0])
        utilization = int(gpu.find('utilization/gpu_util').text.split(' ')[0])
        processes = gpu.findall('processes')[0]

        pids = [process.find('pid').text for process in processes]
        memory = [int(process.find('used_memory').text.split(' ')[0]) for process in processes]
        proc_type = [process.find('type').text for process in processes]
        gpu_infos.append({'idx': idx, 'model': model,
                          'pids': pids,
                          'proc_type': proc_type,
                          'memory': memory,
                          'total_memory': total_memory,
                          'total_used_memory': total_used_memory,
                          'utilization': utilization})

    return gpu_infos


def filter_gpu_info(gpu_info, graphical=True, cuda=True):
    """Given a gpu info dictionary with pids, proc_type and memory, filter the process info based on the flags.
    The flags specify whether to keep only graphical or cuda processes or both."""
    accepted_proc_types = []
    if graphical:
        accepted_proc_types += ['G']
    if cuda:
        accepted_proc_types += ['C']

    gpu_info = dict(**gpu_info)  # copy the contents
    # filter the process info
    gpu_info['pids'] = [elem for elem, proc_type in zip(gpu_info['pids'], gpu_info['proc_type'])
                        if proc_type in accepted_proc_types]
    gpu_info['memory'] = [elem for elem, proc_type in zip(gpu_info['memory'], gpu_info['proc_type'])
                          if proc_type in accepted_proc_types]
    gpu_info['proc_type'] = [elem for elem, proc_type in zip(gpu_info['proc_type'], gpu_info['proc_type'])
                             if proc_type in accepted_proc_types]
    return gpu_info


def print_free_gpus(server, gpu_infos):
    free_gpus = [info for info in gpu_infos if len(info['pids']) == 0]

    if len(free_gpus) == 0:
        info('Server {}: No free GPUs :('.format(server))
    else:
        info('Server {}:'.format(server))
        for gpu_info in free_gpus:
            info('\tGPU {}, {}'.format(gpu_info['idx'], gpu_info['model']))


def get_memory_usage(gpu_info, users_by_pid):
    memory_usage = {}
    for pid, memory in zip(gpu_info['pids'], gpu_info['memory']):
        memory_usage[users_by_pid[pid]] = memory_usage.get(users_by_pid[pid], 0) + memory

    return memory_usage


def print_gpu_infos(server, gpu_infos, run_ps, run_get_real_names,
                    filter_by_user=None,
                    translate_to_real_names=False,
                    show_utilization=False):
    # get pids of processes using the gpu and get their corresponding users
    pids = [pid for gpu_info in gpu_infos for pid in gpu_info['pids']]
    if len(pids) > 0:
        ps = run_ps(pids=pids)
        if ps is None:
            error('Could not reach {} or error running ps'.format(server))
            return

        users_by_pid = get_users_by_pid(ps)
    else:
        users_by_pid = {}

    # find full names of users
    if translate_to_real_names:
        all_users = set((users_by_pid[pid] for gpu_info in gpu_infos
                         for pid in gpu_info['pids']))
        real_names_by_users = run_get_real_names(users=all_users)

    # print the server name
    info('Server {}:'.format(server))

    # build and print info for each server
    gpu_text_data = []
    for gpu_info in gpu_infos:
        users = set((users_by_pid[pid] for pid in gpu_info['pids']))
        memory_used_by_user = get_memory_usage(gpu_info, users_by_pid)
        used_memory = gpu_info['total_used_memory']

        if filter_by_user is not None and filter_by_user not in users:
            continue

        # build the gpu info string
        if len(gpu_info['pids']) == 0:
            status = 'Free'
        else:
            user_texts = []
            for user in users:
                user_text = '{}'.format(user)  # add the user name at first

                # build extra string
                extra_texts = []
                if translate_to_real_names:
                    extra_texts.append('{}'.format(real_names_by_users[user]))
                if show_utilization:
                    extra_texts.append('{} MiB'.format(memory_used_by_user[user]))

                # append extra text if any
                if len(extra_texts) != 0:
                    user_text += ' ({})'.format(', '.join(extra_texts))

                user_texts.append(user_text)  # add it to the list

            status = 'Used by {}'.format(', '.join(user_texts))

        # build the entire line
        data_entries = [
            '\tGPU {}'.format(gpu_info['idx']),
        ]

        # show memory and utilization or just model
        if show_utilization:
            data_entries += [
                '({},'.format(gpu_info['model']),
                '{}%,'.format(gpu_info['utilization']),
                '{}/{}'.format(used_memory, gpu_info['total_memory']),
                'MiB):',
            ]
        else:
            data_entries += ['({})'.format(gpu_info['model'])]

        data_entries += [status]  # append the status at the end
        gpu_text_data.append(data_entries)  # add another line

    # print them, aligned
    info(format_aligned(gpu_text_data))


def run_cmd(argv):
    args = parser.parse_args(argv)
    logging.basicConfig(format='%(message)s',
                        level=logging.DEBUG if args.verbose else logging.INFO)

    if len(args.servers) == 0:
        try:
            debug('Using server file {}'.format(args.server_file))
            with open(args.server_file, 'r') as f:
                servers = (s.strip() for s in f.readlines())
                args.servers = [s for s in servers if s != '']
        except OSError as e:
            error('Could not open server file {}'.format(args.server_file))
            return

    if len(args.servers) == 0:
        error(('No GPU servers to connect to specified.\nPut addresses in '
               'the server file or specify them manually as an argument'))
        return

    if args.ssh_user is not None:
        args.servers = ['{}@{}'.format(args.ssh_user, server)
                        for server in args.servers]
    if args.me:
        if args.ssh_user is not None:
            args.user = args.ssh_user
        else:
            args.user = pwd.getpwuid(os.getuid()).pw_name
    if args.user or args.finger:
        args.list = True

    for server in args.servers:
        if server == '.' or server == 'localhost' or server == '127.0.0.1':
            run_nvidiasmi = run_nvidiasmi_local
            run_ps = run_ps_local
            run_get_real_names = get_real_names_local
        else:
            run_nvidiasmi = partial(run_nvidiasmi_remote,
                                    server=server,
                                    ssh_timeout=args.ssh_timeout,
                                    cmd_timeout=args.cmd_timeout)
            run_ps = partial(run_ps_remote,
                             server=server,
                             ssh_timeout=args.ssh_timeout,
                             cmd_timeout=args.cmd_timeout)
            run_get_real_names = partial(get_real_names_remote,
                                         server=server,
                                         ssh_timeout=args.ssh_timeout,
                                         cmd_timeout=args.cmd_timeout)

        nvidiasmi = run_nvidiasmi()
        if nvidiasmi is None:
            error(('Could not reach {} or '
                   'error running nvidia-smi').format(server))
            continue

        gpu_infos = get_gpu_infos(nvidiasmi)

        # filter info
        graphical_flag = (not (args.graphical or args.cuda)) or args.graphical
        cuda_flag = (not (args.graphical or args.cuda)) or args.cuda

        gpu_infos = [filter_gpu_info(gpu_info, graphical=graphical_flag, cuda=cuda_flag)
                     for gpu_info in gpu_infos]

        if args.list:
            print_gpu_infos(server, gpu_infos, run_ps, run_get_real_names,
                            filter_by_user=args.user,
                            translate_to_real_names=args.finger,
                            show_utilization=args.utilization)
        else:
            print_free_gpus(server, gpu_infos)


def main():
    run_cmd(sys.argv[1:])


if __name__ == '__main__':
    main()
