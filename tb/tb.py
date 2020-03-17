#!/usr/bin/env python3

import json, os, sys, yaml, hcl
from fuzzywuzzy import fuzz
import argparse, glob
from subprocess import Popen, PIPE
from pyfiglet import Figlet

from collections import OrderedDict
import re

from git import Repo, Remote, InvalidGitRepositoryError
import time

PACKAGE = "tb"
LOG = True
DEBUG=False

def anyof(needles, haystack):
    for n in needles:
        if n in haystack:
            return True

    return False

def log(s):
    if LOG == True:
        print (s)

def debug(s):
    if DEBUG == True:
        print (s)

def run(cmd, splitlines=False, env=os.environ, raise_exception_on_fail=False):
    # you had better escape cmd cause it's goin to the shell as is
    proc = Popen([cmd], stdout=PIPE, stderr=PIPE, universal_newlines=True, shell=True, env=env)
    out, err = proc.communicate()
    if splitlines:
        out_split = []
        for line in out.split("\n"):
            line = line.strip()
            if line != '':
                out_split.append(line)
        out = out_split

    exitcode = int(proc.returncode)

    if raise_exception_on_fail and exitcode != 0:
        raise Exception("Running {} resulted in return code {}, below is stderr: \n {}".format(cmd, exitcode, err))

    return (out, err, exitcode)

def runshow(cmd, env=os.environ):
    # you had better escape cmd cause it's goin to the shell as is

    stdout = sys.stdout
    stderr = sys.stderr

    if LOG != True:
        stdout = None
        strerr = None

    proc = Popen(cmd, stdout=stdout, stderr=stderr, shell=True, env=env)
    proc.communicate()

    exitcode = int(proc.returncode)

    return exitcode

def flatwalk_up(haystack, needle):
    results = []
    spl = needle.split("/")
    needle_parts = [spl.pop(0)]
    for s in spl:
        add = "/".join([needle_parts[-1],s])
        needle_parts.append(add)

    for (folder, fn) in flatwalk(haystack):
        for n in needle_parts:
            if folder.endswith(n):
                results.append((folder, fn))
                break
        if folder == haystack:
            results.append((folder, fn))

    for (folder, fn) in results: 
        yield (folder, fn)

def flatwalk(path):
    for (folder, b, c) in os.walk(path):
        for fn in c:
            yield (folder, fn)

def dir_is_git_repo(dir):
    try:
        repo = Repo(dir)
        return True

    except InvalidGitRepositoryError:
        pass

    return False

def git_rootdir(dir="."):
    if dir_is_git_repo(dir):
        return dir
    else:
        #print (wdir)
        oneup = os.path.abspath(dir+'/../')
        if oneup != "/":
            #print ("trying {}".format(oneup))
            return git_rootdir(oneup)
        else:
            # not a git repository
            return None

def git_check(wdir='.'):
    
    git_root = git_rootdir(wdir)
    f = "{}/.git/FETCH_HEAD".format(os.path.abspath(git_root))
    
    if os.path.isfile(f):
        '''
         make sure this is not a freshly cloned repo with no FETCH_HEAD
        '''
        last_fetch = int(os.stat(f).st_mtime)
        diff = int(time.time() - last_fetch)
    else:
        # if the repo is a fresh clone, there is no FETCH_HEAD
        # so set time diff to more than a minute to force a fetch
        diff = 61
        
    repo = Repo(git_root)

    assert not repo.bare

    remote_names = []
    
    # fetch at most once per minute
    for r in repo.remotes:
        remote_names.append(r.name)
        if diff > 60:
            remote = Remote(repo, r.name)
            remote.fetch()
        
    # check what branch we're on
    branch = repo.active_branch.name
        
    origin_branch = None
    for ref in repo.git.branch('-r').split('\n'):
        for rn in remote_names:
            if "{}/{}".format(rn, branch) in ref:
                origin_branch = ref.strip()
                break
        
        
    if origin_branch == None:
        # no remote branch to compare to
        return 0
        
    # check if local branch is ahead and /or behind remote branch
    command = "git -C {} rev-list --left-right --count \"{}...{}\"".format(git_root, branch, origin_branch)
    #print command
    (ahead_behind, err, exitcode) = run(command, raise_exception_on_fail=True)
    ahead_behind = ahead_behind.strip().split("\t")
    ahead = int(ahead_behind[0])
    behind = int(ahead_behind.pop())
    
    if behind > 0:
        sys.stderr.write("")
        sys.stderr.write("GIT ERROR: You are on branch {} and are behind the remote.  Please git pull and/or merge before proceeding.  Below is a git status:".format(branch))
        sys.stderr.write("")
        (status, err, exitcode) = run("git -C {} status ".format(git_root))
        sys.stderr.write(status)
        sys.stderr.write("")
        return(-1)
    else:
    
        TB_GIT_DEFAULT_BRANCH = os.getenv('TB_GIT_DEFAULT_BRANCH', 'master')
        
        if branch != TB_GIT_DEFAULT_BRANCH:
            '''
                in this case assume we're on a feature branch
                if the FB is behind master then issue a warning
            '''
            command = "git -C {} branch -vv | grep {} ".format(git_root, TB_GIT_DEFAULT_BRANCH)
            (origin_master, err, exitcode) = run(command)
            if exitcode != 0:
                '''
                In this case the git repo does not contain TB_GIT_DEFAULT_BRANCH, so I guess assume that we're 
                on the default branch afterall and that we're up to date persuant to the above code
                '''
                return 0
            
            for line in origin_master.split("\n"):
                if line.strip().startswith(TB_GIT_DEFAULT_BRANCH):
                    origin = line.strip().split('[')[1].split('/')[0]

            assert origin != None

            command = "git -C {} rev-list --left-right --count \"{}...{}/{}\"".format(git_root, branch, origin, TB_GIT_DEFAULT_BRANCH)
            (ahead_behind, err, exitcode) = run(command)
            ahead_behind = ahead_behind.strip().split("\t")
            ahead = int(ahead_behind[0])
            behind = int(ahead_behind.pop())

            command = "git -C {} rev-list --left-right --count \"{}...{}\"".format(git_root, branch, TB_GIT_DEFAULT_BRANCH)
            (ahead_behind, err, exitcode) = run(command)
            ahead_behind = ahead_behind.strip().split("\t")
            local_ahead = int(ahead_behind[0])
            local_behind = int(ahead_behind.pop())

            
            if behind > 0:
                sys.stderr.write("")
                sys.stderr.write("GIT WARNING: Your branch, {}, is {} commit(s) behind {}/{}.\n".format(branch, behind, origin, TB_GIT_DEFAULT_BRANCH))
                sys.stderr.write("This action may clobber new changes that have occurred in {} since your branch was made.\n".format(TB_GIT_DEFAULT_BRANCH))
                sys.stderr.write("It is recommended that you stop now and merge or rebase from {}\n".format(TB_GIT_DEFAULT_BRANCH))
                sys.stderr.write("\n")
                
                if ahead != local_ahead or behind != local_behind:
                    sys.stderr.write("")
                    sys.stderr.write("INFO: your local {} branch is not up to date with {}/{}\n".format(TB_GIT_DEFAULT_BRANCH, origin, TB_GIT_DEFAULT_BRANCH))
                    sys.stderr.write("HINT:")
                    sys.stderr.write("git checkout {} ; git pull ; git checkout {}\n".format(TB_GIT_DEFAULT_BRANCH, branch))
                    sys.stderr.write("\n")
                    
                answer = raw_input("Do you want to continue anyway? [y/N]? ").lower()
                
                if answer != 'y':
                    log("")
                    log("Aborting due to user input")
                    exit()
            
        return 0

class WrapTerragrunt():

    def __init__(self):

        self.tg_bin = os.getenv("TERRAGRUNT_BIN", "terragrunt")
        self.tf_bin = os.getenv("TERRAFORM_BIN", "terraform")
        self.terragrunt_options = []
        self.quiet = False


    def get_cache_dir(ymlfile, package_name):
        cache_slug = os.path.abspath(ymlfile)
        debug(cache_slug)
        return  os.path.expanduser('~/.{}_cache/{}'.format(package_name, hashlib.sha224(cache_slug).hexdigest()))

    def set_option(self, option):
        self.terragrunt_options.append(option)

    def set_quiet(self, which=True):
        self.quiet = which

    def get_download_dir(self):
        return os.getenv('TERRAGRUNT_DOWNLOAD_DIR',"~/.terragrunt")

    def set_iam_role(self, iam_role):
        self.set_option("--terragrunt-iam-role {} ".format(iam_role))

    def get_command(self, command, wdir=".", var_file=None, extra_args=[]):

        self.set_option("--terragrunt-download-dir {}".format(self.get_download_dir()))
        # path to terraform
        self.set_option("--terragrunt-tfpath {}".format(self.tf_bin))

        if var_file != None:
            var_file = "-var-file={}".format(var_file)
        else:
            var_file = ""

        cmd = "{} {} --terragrunt-source-update --terragrunt-working-dir {} {} {} {} ".format(self.tg_bin, command, wdir, var_file, " ".join(set(self.terragrunt_options)), " ".join(extra_args))
        
        if self.quiet:
            cmd += " > /dev/null 2>&1 "
        
        debug("running command:\n{}".format(cmd))
        return cmd

class Project():

    def __init__(self,
        git_filtered=os.getenv('TB_GIT_FILTER', "False").lower()  in ("on", "true", "1"),
        conf_marker="project.yml",
        inpattern=".hclt",
        dir=os.getcwd()):

        self.inpattern=inpattern
        self.dir=dir
        self.vars=None
        self.parse_messages = []

        self.components = None
        self.git_filtered = git_filtered
        self.conf_marker = conf_marker

    def set_dir(self, dir):
        self.dir=dir
        self.vars = None

    def check_hclt_file(self, path):
        only_whitespace = True
        with open(path, 'r') as lines:
            for line in lines:    
                #debug("##{}##".format(line.strip()))     
                if line.strip() != "":
                    only_whitespace = False
                    break
        #debug(only_whitespace)     

        if not only_whitespace:
            with open(path, 'r') as fp:
                try:
                    obj = hcl.load(fp)
                except:
                    raise Exception("FATAL: An error occurred while parsing {}\nPlease verify that this file is valid hcl syntax".format(path))

        return only_whitespace

    def check_parsed_file(self):
        # this function makes sure that self.outstring contains a legit hcl file with a remote state config
        obj = hcl.loads(self.out_string)

        debug(obj)
        try:
            d = obj["remote_state"]
        except KeyError:
            return "No remote_state block found"

        return True

    def format_hclt_file(self, path):
        log("Formatting {}".format(path))
        only_whitespace = self.check_hclt_file(path)
        if not only_whitespace:
            cmd = "cat \"{}\" | terraform fmt -".format(path)
            (out, err, exitcode) = run(cmd, raise_exception_on_fail=True)

            with open(path, 'w') as fh:
                fh.write(out)
                

    def example_commands(self, command):
        log("")

        for which, component in self.get_components():     
            log("{} {} {}".format(PACKAGE, command, component))
        log("")
        
    def get_project_root(self, dir=".", fallback_to_git=True):
        d = os.path.abspath(dir)

        if os.path.isfile("{}/{}".format(d, self.conf_marker)):
            return dir
        if fallback_to_git and dir_is_git_repo(dir):
            return dir
        
        oneup = os.path.abspath(dir+'/../')
        if oneup != "/":
            return self.get_project_root(oneup, fallback_to_git)
        
        raise "Could not find a project root directory"

   # def get_filtered_components(wdir, filter):

    def get_components(self, dir='.'):
        if self.components == None:
            self.components = []
            filtered = []
            if self.git_filtered:
                (out, err, exitcode) = run("git status -s -uall", raise_exception_on_fail=True)
                for line in out.split("\n"):
                    p = line.split(" ")[-1]
                    if len(p) > 3:
                        filtered.append(os.path.dirname(p))

            for (dirpath, filename) in flatwalk('.'):
                dirpath = dirpath[2:]
                if filename in ['terragrunt.hclt', "bundle.yml"] and len(dirpath) > 0:
                    which = "component"
                    if filename == "bundle.yml":
                        which = "bundle"
                    if self.git_filtered:
                        match = False
                        for f in filtered:
                            if f.startswith(dirpath):
                                match = True
                                break
                        if match:
                            self.components.append((which, dirpath))

                    else:
                        self.components.append((which, dirpath))
        
        return self.components
    


    def component_type(self, component, dir='.'):
        for which, c in self.get_components(dir=dir):
            if c == component:
                return which

        return None


    def get_bundle(self, wdir):
        components = []

        if wdir[-1] == "*":
            debug("")
            debug("get_bundle wdir {}".format(wdir))
            wdir = os.path.relpath(wdir[0:-1])
            for which, c in self.get_components():
                if c.startswith(wdir):
                    components.append(c)

                    debug("get_bundle  {}".format(c))
            debug("")
            return components

        bundleyml = '{}/{}'.format(wdir, "bundle.yml")

        if not os.path.isfile(bundleyml):
            return [wdir]

        with open(bundleyml, 'r') as fh:
            d = yaml.load(fh, Loader=yaml.FullLoader)

        order = d['order']

        if type(order) == list:
            for i in order:
                component = "{}/{}".format(wdir, i)
                if self.component_type(component, wdir) == "component":
                    components.append(component)
                else:
                    for c in  self.get_bundle(component):
                        components.append(c)

        return components

    def check_hclt_files(self):
        for f in self.get_files():
            debug("check_hclt_files() checking {}".format(f))
            self.check_hclt_file(f)

    def get_files(self):
        git_root = self.get_project_root(self.dir)
        for (folder, fn) in flatwalk_up(git_root, self.dir):
            if fn.endswith(self.inpattern):
                yield "{}/{}".format(folder, fn)

    def get_yml_vars(self):
        if self.vars == None:
            git_root = self.get_project_root(self.dir)
            self.vars={}
            for (folder, fn) in flatwalk_up(git_root, self.dir):
                if fn.endswith('.yml'):

                    with open(r'{}/{}'.format(folder, fn)) as fh:
                        d = yaml.load(fh, Loader=yaml.FullLoader)

                        for k,v in d.items():
                            if type(v) in (str, int, float):
                                self.vars[k] = v

    def save_outfile(self):
        with open(self.outfile, 'w') as fh:
            fh.write(self.hclfile)

    @property
    def outfile(self):
        return "{}/{}".format(self.dir, "terragrunt.hcl")

    @property
    def component_path(self):
        abswdir = os.path.abspath(self.dir)
        absroot = self.get_project_root(self.dir)

        return abswdir[len(absroot)+1:]

    def get_template(self):
        self.templates = OrderedDict()
        for f in self.get_files():
            data = u""
            with open(f, 'r') as lines:
                for line in lines:         
                    data += line
                self.templates[os.path.basename(f)] = {
                    "filename": f,
                    "data" : data
                }

    @property
    def tfvars_env(self):
        self.get_yml_vars()
        en = {}

        # self.vars
        for (k, v) in  self.vars.items():
            en['TF_VAR_{}'.format(k)] = v

        # ENV VARS
        for (k, v) in  os.environ.items():
            en['TF_VAR_{}'.format(k)] = v

        return en

    @property
    def tfvars_tf(self):
        out = []
        for (k,v) in self.tfvars_env.items():
            s = "variable \"{}\" ".format(k[7:]) + '{default = ""}'
            out.append(s)

        return "\n".join(out)

    def parse(self):

        self.check_hclt_files()
        self.get_yml_vars()
        self.get_template()

        self.out_string=u""

        # special vars
        self.vars["COMPONENT_PATH"] = self.component_path
        self.vars["COMPONENT_DIRNAME"] = self.component_path.split("/")[-1]
        self.vars["TB_INSTALL_PATH"] = os.path.dirname(os.path.abspath(os.readlink(__file__)))

        self.parse_messages = []
        regex = r"\$\{(.+?)\}"

        for fn,d in self.templates.items():
            # self.vars
            for (k, v) in  self.vars.items():
                d['data'] = d['data'].replace('${' + k + '}', v)

            # ENV VARS
            for (k, v) in  os.environ.items():
                d['data'] = d['data'].replace('${' + k + '}', v)


            # now make sure that all vars have been replaced
            # exclude commented out lines from check
            linenum = 0
            msg = None
            for line in d['data'].split("\n"):
                linenum += 1
                try:
                    if line.strip()[0] != '#':

                        matches = re.finditer(regex, line)

                        for matchNum, match in enumerate(matches):
                            miss = match.group()

                            msg = "{} line {}:".format(d['filename'], linenum)
                            msg += "\n   No substitution found for {}".format(miss)

                            lim = 80
                            near_matches = {}
                            for k in self.vars.keys():
                                ratio = fuzz.ratio(miss, k)
                                if ratio >= lim:
                                    near_matches[k] = ratio

                            for k in os.environ.keys():
                                ratio = fuzz.ratio(miss, k)
                                if ratio >= lim:
                                    near_matches[k] = ratio

                            for k,ratio in near_matches.items():
                                msg += "\n   ==>  Perhaps you meant ${"+k+"}?"

                            msg += "\n"
                            self.parse_messages.append(msg)

                except IndexError: # an empty line has no first character ;)
                    pass
         

            self.out_string += d['data']
            self.out_string += "\n"

    @property
    def parse_status(self):
        if len(self.parse_messages) == 0:
            return True

        return "\n".join([u"Could not substitute all variables in templates 😢"] + self.parse_messages)
        

    @property
    def hclfile(self):
        self.parse()
        return self.out_string

def main(argv=[]):

    epilog = """The following arguments can be activated using environment variables:

    export TB_DEBUG=y                   # activates debug messages
    export TB_APPLY=y                   # activates --force
    export TB_APPROVE=y                 # activates --force
    export TB_GIT_CHECK=y               # activates --git-check
    export TB_NO_GIT_CHECK=y            # activates --no-git-check
    export TB_MODULES_PATH              # required if using --dev

    """
    #TGARGS=("--force", "-f", "-y", "--yes", "--clean", "--dev", "--no-check-git")

    f = Figlet(font='slant')

    parser = argparse.ArgumentParser(description='{}\nTB, facilitates calling terragrunt with nifty features n such.'.format(f.renderText('terrabuddy')),
    add_help=True,
    epilog=epilog,
    formatter_class=argparse.RawTextHelpFormatter)

    #parser.ArgumentParser(usage='Any text you want\n')

    # subtle bug in ArgumentParser... nargs='?' doesn't work if you parse something other than sys.argv,
    parser.add_argument('command', default=None, nargs='*', help='terragrunt command to run (apply, destroy, plan, etc)')

    #parser.add_argument('--dev', default=None, help="if in dev mode, which dev module path to reference (TB_MODULES_PATH env var must be set and point to your local terragrunt repository path)")
    parser.add_argument('--downstream-args', default=None, help='optional arguments to pass downstream to terragrunt and terraform')

    # booleans
    parser.add_argument('--clean', dest='clean', action='store_true', help='clear all cache')
    parser.add_argument('--force', '--yes', '-t', '-f', action='store_true', help='Perform terragrunt action without asking for confirmation (same as --terragrunt-non-interactive)')
    parser.add_argument('--dry', action='store_true', help="dry run, don't actually do anything")
    parser.add_argument('--no-check-git', action='store_true', help='Explicitly skip git repository checks')
    parser.add_argument('--check-git', action='store_true', help='Explicitly enable git repository checks')
    parser.add_argument('--quiet', action='store_true', help='suppress output except fatal errors')
    parser.add_argument('--json', action='store_true', help='When applicable, output in json format')
    parser.add_argument('--debug', action='store_true', help='display debug messages')

    clear_cache = False

    args = parser.parse_args(args=argv)
    # TODO add project specific args to project.yml

    global LOG


    if args.quiet or args.json:
        LOG = False

    # grab args
    
    if len(args.command) < 2:
        log("ERROR: no command specified, see help")
        return(-1)
    else:
        command = args.command[1]

    CHECK_GIT = True
    if command[0:5] in ('apply', 'destr'):
        # [0:5] to also include "*-all" command variants
        CHECK_GIT = True

    if args.check_git or os.getenv('TB_GIT_CHECK', 'n')[0].lower() in ['y', 't', '1']:
        CHECK_GIT = True

    if args.no_check_git or os.getenv('TB_NO_GIT_CHECK', 'n')[0].lower() in ['y', 't', '1'] :
        CHECK_GIT = False

    if args.debug or os.getenv('TB_DEBUG', 'n')[0].lower() in ['y', 't', '1'] :
        global DEBUG
        DEBUG = True

    # check git
    if CHECK_GIT:
        gitstatus = git_check()
        if gitstatus != 0:
            return gitstatus


    project = Project()
    wt = WrapTerragrunt()

    #TODO add "env" command to show the env vars with optional --export command for exporting to bash env vars

    if command == "format":
        for (dirpath, filename) in flatwalk('.'):
            if filename.endswith('.hclt'):
                project.format_hclt_file("{}/{}".format(dirpath, filename))

    if command in ("plan", "apply", "destroy", "refresh", "show"):

        try:
            wdir = os.path.relpath(args.command[2])
        except:
            log("OOPS, no component specified, try one of these:")
            project.example_commands(command)
            return(100)

        if not os.path.isdir(wdir):
            log("ERROR: {} is not a directory".format(wdir))
            return -1
        
        project.set_dir(wdir)

        force = args.force

        # -auto-approve and refresh do not mix
        if command in ["refresh"]:
            force = False

        if force:
            wt.set_option("--terragrunt-non-interactive")
            wt.set_option("-auto-approve")

        if args.quiet:
            wt.set_quiet()

        t = project.component_type(component=wdir)
        if t == "component":
            project.parse()
            project.save_outfile()
            check = project.check_parsed_file()
            if check != True:
                print ("An error was found after parsing {}: {}".format(project.outfile, check))
                return 110


            if project.parse_status != True:
                print (project.parse_status)
                return (120)

            if not args.dry:               
                runshow(wt.get_command(command=command, wdir=wdir))
        elif t == "bundle":
            log("Performing {} on bundle {}".format(command, wdir))
            log("")
            # parse first
            parse_status = []
            components = project.get_bundle(wdir)
            for component in components:
                project.set_dir(component)
                project.parse()
                project.save_outfile()
                if project.parse_status != True:

                    parse_status.append(project.parse_status)

            if len(parse_status) > 0:
                print("\n".join(parse_status))
                return (120)

            # run terragrunt per component
            for component in components:                

                log("{} {} {}".format(PACKAGE, command, component))
                if args.dry:
                    continue

                if command == "show":
                    continue

                retcode = runshow(wt.get_command(command=command, wdir=component))

                if retcode != 0:
                    log("Got a non zero return code running component {}, stopping bundle".format(component))
                    return retcode

            if command in ['apply', "show"]:
                log("")
                log("")

                # grab outputs of components
                out_dict = []
                
                # fresh instance of WrapTerragrunt to clear out any options from above that might conflict with show
                wt = WrapTerragrunt()
                if args.json:
                    wt.set_option('-json')

                for component in components:

                    out, err, retcode = run(wt.get_command(command="show", wdir=component), raise_exception_on_fail=True)

                    if args.json:
                        d = json.loads(out)
                        out_dict.append({
                            "component" : component,
                            "outputs" : d["values"]["outputs"]})
                    else:
                        debug((out, err, retcode))

                        lines = []

                        p = False
                        for line in out.split("\n"):

                            if p:
                                lines.append("    {}".format(line))
                            if line.strip().startswith('Outputs:'):
                                debug("Outputs:; p = True")
                                p = True

                        txt = "| {}".format(component)
                        print("-" * int(len(txt)+3))
                        print(txt)
                        print("-" * int(len(txt)+3))

                        if len(lines) > 0:
                            print("  Outputs:")
                            print("")
                            for line in lines:
                                print(line)

                        else:
                            print("No remote state found")
                        print("")
                if args.json:
                    print(json.dumps(out_dict, indent=4))

        else:
            log("ERROR {}: this directory is neither a component nor a bundle, nothing to do".format(wdir))
            return 130
            


if __name__ == '__main__':
    retcode = main(sys.argv)
    exit(retcode)


"""

def get_terragrunt_download_dir():
    terragrunt_dl_dir = " ~/.terragrunt"
    try:
        terragrunt_dl_dir = os.environ['TERRAGRUNT_DOWNLOAD_DIR']
    except:
        pass
    return terragrunt_dl_dir





        self.set_tg_option("--terragrunt-download-dir {}".format(get_terragrunt_download_dir()))


        TB_BIN = self.get_terragrunt_bin(WDIR)
        TF_BIN = self.get_terraform_bin(WDIR)
        self.set_tg_option("--terragrunt-tfpath {}".format(TF_BIN))


    def get_bin(self, which, wdir):
        if not os.path.isdir(os.path.abspath(wdir)):
            raise Exception("ERROR: {} is not a dir".format(wdir))
        elif os.path.isfile(wdir + "/" + "terraform.tfvars"):
            if which == "terraform":
                debug("{} requires terraform@0.11".format(wdir))
                return self.TF011_BIN
            else:
                debug("{} requires terragrunt@0.18".format(wdir))
                return self.TG018_BIN

        elif os.path.isfile(wdir + "/" + "terragrunt.hcl"):

            if which == "terraform":
                return self.TF_BIN
            else:
                return self.TB_BIN

        else:
            if self.command[-4:] == "-all":
                # scan every subdir looking for modules to tell us what version to use :)
                answers = []
                for i in os.listdir(wdir):
                    p = "{}/{}".format(wdir, i)
                    if os.path.isdir(p):
                        try:
                            answers.append(self.get_bin(which, p))
                        except:
                            pass

                if len(answers) == 0:
                    raise Exception("ERROR: No modules found in {}".format(wdir))

                # turning into a set makes the values unique
                if len(set(answers)) == 1:
                    # only one unique answer
                    return answers[0]

                # if you make it here then LOL
                raise Exception("ERROR: Cannot run {} in {}.  Some modules contain newer terragrunt.hcl files whereas others have the old style terraform.tfvars.  You must run individual {} commands.".format(self.command, wdir, self.command.split('-')[0]))

            else:
                raise Exception("ERROR: {} is not a module".format(wdir))





    def get_terragrunt_bin(self, wdir):
        return self.get_bin("terragrunt", wdir)

    def get_terraform_bin(self, wdir):
        return self.get_bin("terraform", wdir)


"""