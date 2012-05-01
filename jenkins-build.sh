svn co https://katfs.kat.ac.za/svnDS/code/tools
tools/kat_build_virtualenv.py -s checkout/system-requirements.txt -t venv
. venv/bin/activate
## Install other svn dependencies
# None that I am aware of for katcp

## Install self
pip install ./checkout
