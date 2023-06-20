# GP2040-CE Binary Tools

Tools for working with GP2040-CE binary dumps.

## Installation

```
% git clone [URL to this repository]
% cd gp2040ce-binary-tools
% pip install -e .
```

At some point we may publish packages to e.g. pypi.

### Development Installation

As above, plus also `pip install -Ur requirements/requirements-dev.txt` to get linters and whatnot.

## Tools

### visualize-storage

**visualize-storage** reads a dump of a GP2040-CE board's flash storage section, where the configuration lives,
and prints it out for visual inspection or diffing with other tools. Usage is simple; just pass the tool your
binary file to analyze along with the path to the Protobuf files.

Because Protobuf relies on .proto files to convey the serialized structure, you must supply them
from the main GP2040-CE project, e.g. pointing this tool at your clone of the core project. Something like
this would suffice for a working invocation (note: you do not need to compile the files yourself):

```
% visualize-storage --proto-files-path=~/proj/GP2040-CE/proto \
--proto-files-path=~/proj/GP2040-CE/lib/nanopb/generator/proto \
memory.bin
```

In the future we will look into publishing complete packages that include the compiled `_pb2.py` files, so that you
don't need to provide them yourself.
