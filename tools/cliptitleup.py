#!/usr/bin/python3
import re

import pyperclip

original_string = pyperclip.paste()

string_no_punc = re.sub(r'[^\w\s]', '', original_string)
final_string = string_no_punc.title()

pyperclip.copy(final_string)
