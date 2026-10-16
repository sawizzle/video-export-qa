try:  # Skill file extractor. One try block: a copy cut short is a SyntaxError, never a silent run.
 import hashlib, itertools, json, os, re, stat, sys, tempfile
 SKILL, PREFIX, TABLE = "__SKILL__", "__PREFIX__", {
  # __TABLE__
 }
 DIRS, POSIX, J = sorted({n.split("/")[0] for n in TABLE}), os.name != "nt", os.path.join
 HEAD = re.compile(r"^#{2,3} File: [^\r\n]*", re.M)
 def stop(why): sys.exit("REFUSED: " + why)
 def show(p): return str(p) if re.fullmatch(r"[\w .:/\x5c~+-]+", str(p), re.A) else ascii(str(p))
 def same(n, data): return (hashlib.sha256(data).hexdigest(), len(data)) == TABLE[n]
 def tempdir():
   t = tempfile.gettempdir()
   s = os.stat(t)
   if os.path.samefile(t, os.getcwd()): stop("the working folder is the temp folder, or no temp folder is usable: change to another folder (such as your home folder) and run again")
   if POSIX and (s.st_uid not in (0, os.geteuid()) or s.st_mode & 0o022 and not s.st_mode & stat.S_ISVTX): stop("other users can replace folders in the temp folder " + show(t))
   return t
 def entry(p, d):
   s = os.lstat(p)
   if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 1024: stop(show(p) + " is a link or junction")
   if not (stat.S_ISDIR if d else stat.S_ISREG)(s.st_mode): stop(show(p) + " is not a real " + ("folder" if d else "file"))
   if POSIX and s.st_uid != os.geteuid(): stop(show(p) + " belongs to another user")
   if POSIX and (s.st_mode & (0o077 if d else 0o002) or not d and s.st_nlink != 1): stop(show(p) + " is open to other users or has a hard link")
 def verify(arg, need=()):
   f = os.path.abspath(arg)
   if not re.fullmatch(re.escape(PREFIX) + "[a-z0-9_]{8}", os.path.basename(f)): stop(show(f) + " is not a folder that new makes")
   entry(f, True)
   if not os.path.samefile(os.path.dirname(f), tempdir()): stop(show(f) + " is not directly in the temp folder")
   if sorted(os.listdir(f)) != DIRS: stop(show(f) + " must hold only " + ", ".join(DIRS))
   found = []
   for d in DIRS:
     entry(J(f, d), True)
     for n in sorted(d + "/" + x for x in os.listdir(J(f, d))):
       if n not in TABLE: stop(show(f) + " holds " + ascii(n) + " - run scripts with -B" * n.endswith("__pycache__"))
       entry(J(f, n), False)
       with open(J(f, n), "rb") as h:
         if not same(n, h.read(TABLE[n][1] + 1)): stop("%s is not the published file (%d bytes, published %d)" % (n, os.fstat(h.fileno()).st_size, TABLE[n][1]))
       found.append(n)
   if set(need) - set(found): stop("missing: " + ", ".join(sorted(set(need) - set(found))))
   return f, found
 def find(t, n):
   k = TABLE[n][1] + 99
   bodies = [t[:k]]
   for _, m in zip(range(4), re.finditer(r"^#{2,3} File: " + re.escape(SKILL + "/" + n) + r"(?: \(from [^\r\n]*\))?\r?(?![^\n])", t, re.M)):
     for b in {t[m.end() + 1:min(e, m.end() + k)] for e in [h.start() for _, h in zip(range(4), HEAD.finditer(t, m.end()))] + [len(t)]}:
       fence = re.fullmatch(r"\s*(\x60{3,})(?!\x60)[^\n]*\n(.*)\n\1\s*", b, re.S)
       bodies += [b[1:] if b[:1] == "\n" else b] + ([fence.group(2)] if fence else [])
   for c in (v for b in bodies for v in (b, b.rstrip("\n"), b.rstrip("\n") + "\n")):
     data = c.encode("utf-8", "surrogatepass")
     if same(n, data): return data
 def put(arg, saved, names):
   f, found = verify(arg)
   if not stat.S_ISREG(os.stat(saved).st_mode) or os.stat(saved).st_size > 16000000: stop(show(saved) + " is not a file of at most 16 MB")
   with open(saved, "rb") as h:
     texts = [h.read(16000001).decode("utf-8-sig", "replace")]
   try:
     doc = json.loads(texts[0], object_pairs_hook=lambda kv: texts.extend(v for _, v in kv if isinstance(v, str)))
   except (ValueError, RecursionError):
     doc = None
   texts += [doc] * isinstance(doc, str)
   todo = {n: next((d for x in texts for y in dict.fromkeys((x.replace("\r\n", "\n"), x)) for d in [find(y, n)] if d is not None), None) for n in names if n not in found}
   for n in [n for n in todo if todo[n] is None][:1]:
     heads = list(itertools.islice((m.group(0)[:99] for x in texts for m in HEAD.finditer(x)), 5))
     stop("%s holds no exact copy of %s; file headers in it: %s. Nothing was written." % (show(saved), n, ascii(heads)))
   for n, data in todo.items():
     with open(J(f, n), "xb", opener=lambda p, fl: os.open(p, fl, 0o600)) as h:
       h.write(data)
   verify(f, names)
   for n in names: print("OK %s verified (%d bytes)%s" % (n, TABLE[n][1], " - was already there" * (n in found)))
 def run(mode, args):
   if not TABLE or not (mode == "new" and not args or mode == "put" and len(args) > 2 or mode == "check" and args):
     sys.exit(print("usage: python -I -B - new | put FOLDER SAVED NAME... | check FOLDER [NAME...]", file=sys.stderr) or 2)
   i = 1 + (mode == "put")
   args[i:] = [a[len(SKILL) + 1:] if a.startswith(SKILL + "/") else a for a in args[i:]]
   bad = [n for n in args[i:] if n not in TABLE]
   if bad: stop("not a file of " + SKILL + ": " + ", ".join(map(ascii, bad)) + "; its files: " + ", ".join(sorted(TABLE)))
   if mode == "new":
     f = tempfile.mkdtemp(prefix=PREFIX, dir=tempdir())
     for d in DIRS: os.mkdir(J(f, d), 0o700)
     print("OK folder:", show(verify(f)[0]))
   elif mode == "put":
     put(args[0], args[1], args[2:])
   else:
     f, found = verify(args[0], args[1:])
     for n in found: print("OK %s verified (%d bytes)" % (n, TABLE[n][1]))
     print("OK %d verified files and nothing else in %s" % (len(found), show(f)))
 run((sys.argv + [""])[1], sys.argv[2:])
except (OSError, MemoryError) as e:
  sys.exit("REFUSED: the system refused: %s %s" % (type(e).__name__, str(e).encode("ascii", "backslashreplace").decode()))
