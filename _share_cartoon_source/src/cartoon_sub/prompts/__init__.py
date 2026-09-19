from importlib.resources import files

def read(name):
    return files(__package__).joinpath(name).read_text(encoding="utf-8")
