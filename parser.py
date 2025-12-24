# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

from flask import Flask, render_template, request, redirect, url_for, Response
from jinja2 import Environment, meta, exceptions
from random import choice
import inspect
#from cgi import escape
from html import escape
import logging
import logging.handlers
import json
import yaml
import config
import sqlite3
import os
import csv
import tempfile

# local modules (no extensions)
CUSTOM_MODULES = ('netaddr_filters', 'json_query', 'thin_filters')
import importlib
OBJS = [importlib.import_module(custom_filter) for custom_filter in CUSTOM_MODULES]


SQL_FILE = 'jinja_db.sqlite'
app = Flask(__name__)


# ---------------
# utilities: get custom filters and read nested yaml file
# ---------------
def get_custom_filters_description():
    custom_filters = {}
    # getmembers return the name of function and pointer to its description
    for cf in OBJS:
        for m in inspect.getmembers(cf, inspect.isfunction):
            custom_filters[m[0]] = m[1].__doc__ if m[1].__doc__ else ""
    return custom_filters


def get_custom_filters_entrypoints():
    custom_filters_ep = {}
    # getmembers return the name of function and pointer to its description
    for cf in OBJS:
       for m in inspect.getmembers(cf, inspect.isfunction):
            custom_filters_ep[m[0]] = m[1]
    return custom_filters_ep


# Return the path string from an array (items can be empty)
def join_paths(*paths):
   return '/'.join(filter(None, *paths))


# ---------------
# SQL unitary requests : delete, rename and save to csv
# ---------------
def delete(name):
    # print("delete item:", name)
    try:
        conn = sqlite3.connect(SQL_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM templates WHERE name=?", (name,))
        conn.commit()
        conn.close()
        return True
    except sqlite3.Error as er:
        print( 'er:', er.message)
        return "Syntax error in SQL"


def renameto(name_from, name_to, path):
     # print("rename from :", name_from, "to", name_to, "in dir", path )
     try:
         conn = sqlite3.connect(SQL_FILE)
         c = conn.cursor()
         c.execute("UPDATE templates SET name=? WHERE name=?", 
                       (
                        join_paths ((path,name_to)), join_paths ((path,name_from))
                       ) )

         conn.commit()
         conn.close()
         return True
     except sqlite3.Error as er:
         print( 'er:', er.message)
         return "Syntax error in SQL"


def sqlite2csv(csv_file):
    rc = False
    try:
        conn = sqlite3.connect(SQL_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM templates")
        with open(csv_file, 'w+') as f:
           writer = csv.writer(f)
           print(csv_file)
           writer.writerow(['name', 'template', 'params', 'timestamp'])
           for row in cursor:
               print(row[0])
               writer.writerow([ unicode(cell).encode('utf-8')  for cell in row])
        rc = True
    except:
        rc=False

    conn.commit()
    conn.close()
    return rc


# ---------------
# home page: display a demo pattern
# ---------------
@app.route("/")
def home():
    return render_template('index.html', flavour="index", custom_filters=get_custom_filters_description())


# ---------------
# load: read an entry and display it
# ---------------
@app.route('/load/<path:template_path>')
def load(template_path):
    # print("load: path name", template_path)
    try:
        conn = sqlite3.connect(SQL_FILE)
        c = conn.cursor()
        c.execute("SELECT * FROM templates WHERE name=?", (template_path,))
        row = c.fetchone()
        # found ? 
        if row is None:
           return render_template('not_found.html', key=template_path)
        # assign variables to row
        (template_name, template, values, dummytime) = row
        # print(template_name, template, values, dummytime)
        conn.commit()
        conn.close()
        return render_template('load.html', 
                                flavour="load",
                                template=template, 
                                values=values, 
                                sql_template_name=template_name,
                                link = request.base_url,
                                custom_filters=get_custom_filters_description())
    except sqlite3.Error as er:
        print( 'er:', er.message)
        return "Syntax error in SQL"


# ---------------
# list: manage table template content
#   - populate data for the template list.html (GET)
#   - rename and delete database entries (POST)
# ---------------
@app.route('/list/<path:list_path>', methods=['GET', 'POST'])
@app.route('/list/', defaults={'list_path': ''},  methods=['GET', 'POST']  )
@app.route('/list',  defaults={'list_path': ''} )
def name_list(list_path):
    # print("list path=", list_path)

    # POST method, react to action (delete or rename)
    # then execute the get request
    if request.method == 'POST':
        if request.form['action'] == "delete":
              delete(request.form['name'])
        if request.form['action'] == "rename":
              renameto (request.form['entry'], request.form['to'], request.form['path'])


    try:
        # now select 
        #       - all entries that match "{{path}}/*" in request 1
        #       - paths that match "{{path}}/*/"      in request 2

        conn = sqlite3.connect(SQL_FILE)
        c = conn.cursor()
        c.execute("""
                     SELECT name,timestamp 
                            FROM templates 
                            WHERE     name LIKE ?
                                  AND name NOT LIKE ?
                  """, 
                                ('{}/%'.format(list_path),  '{}/%/%'.format(list_path)) 
                       if list_path!=''
                       else     ( '%', '%/%' ) 
                  )
        # reformat output into hash (remember name is a SQL key)
        # key is the short_name (is uniq in a defined path) 
        # may be sorted inside list.html
        entries = {}
        for row in c:
            try:
                full_path, relative_name = row[0].rsplit("/", 1)
            except:
                full_path, relative_name = '', row[0]
            entries[relative_name]  = { 'name': row[0], 'path': full_path, 'timestamp': row[1] } 
        # print("hash entries:", entries)

        # parse all paths and build the current dir structure
        c.execute("""
                     SELECT name 
                             FROM templates 
                             WHERE name LIKE ?
                  """, 
                       	( '{}/%/%'.format(list_path) if list_path!='' else '%/%', ))
        paths = {}
        if list_path!='':
            backpath = join_paths (list_path.split('/')[:-1])
            paths['/']  = { 'link': '' }
            paths['..'] = { 'link': backpath }
        for row in c:
           relative_path = row[0][len(list_path):]
           if relative_path[0]=='/':
               relative_path = relative_path[1:]
           short_path = relative_path.split('/')[0]
           paths[short_path]  = { 'link': join_paths((list_path, short_path)) } 

	# close SQL connectore and return list
        conn.commit()
        conn.close()
        return render_template('list.html', 
                               nb=len(entries), entries=entries, 
                               paths=paths, 
                               current_path=list_path,
                               root=request.url_root)
    except Exception as e:
        print( "Exception {}".format(e.args) )
        return ('<html><body><h2>Internal Error</h2>{}</body></html>'.format(e.args))


# ---------------
# csv: save database in csv format
# ---------------
@app.route('/csv', methods=['GET', 'POST'])
def send_csv():
    tmp_file = tempfile.gettempdir() + "/db.csv"
    if  sqlite2csv(tmp_file):
        with open(tmp_file) as fp:
            csv = fp.read()
        return Response(
                          csv,
                          mimetype="text/csv",
                          headers={"Content-disposition" : "attachment; filename=jinja2_templates.csv"})
         # os.remove(tmp_file)
    else:
        # os.remove(tmp_file)
        return ('<html><body><h2>Internal Error</h2></body></html>')
         
    



# ---------------
# save: record entry into table template
# ---------------
@app.route('/save', methods=['GET', 'POST'])
def save():
    # print("save entry", request.form['sql_template_name'], " into", SQL_FILE)
    try:
        db_key = request.form['sql_template_name']
        conn = sqlite3.connect(SQL_FILE)
        c = conn.cursor()
        # update the SQL entry 
        c.execute("INSERT OR REPLACE INTO templates (name, template, params) VALUES (?, ?, ?)", 
                      (  request.form['sql_template_name'], 
                         request.form['template'], 
                         request.form['values']) )
        conn.commit()
        conn.close()
        # print("entry {} updated".format(db_key))
        # display the same page
        return load(db_key)
    except sqlite3.Error as er:
        print( 'er:', er.message)
        return "Syntax error in SQL"


# ---------------
# convert: render template
# ---------------
@app.route('/convert', methods=['POST'])
def convert():
    jinja2_env = Environment()

    # Load custom filters
    custom_filters = get_custom_filters_entrypoints()
    jinja2_env.filters.update(custom_filters)
    jinja2_env.filters['zip'] = zip

    # Load the template
    try:
        jinja2_tpl = jinja2_env.from_string(request.form['template'])
    except (exceptions.TemplateSyntaxError, exceptions.TemplateError) as e:
        return "Syntax error in jinja2 template: {0}".format(e)


    values = {}
    # Check JSON for errors
    if request.form['input_type'] == "json":
            try:
                values = json.loads(request.form['values']) if request.form['values'] else {}
            except ValueError as e:
                return "Invalid JSON syntax in data:\n----------------------------\n{0}".format(e)
    # Check YAML for errors
    elif request.form['input_type'] == "yaml":
            try:
                values = yaml.safe_load(request.form['values']) if request.form['values'] else {}
            except yaml.YAMLError as exc:
                return "Invalid YAML syntax in data:\n----------------------------\n" + str(exc)
    else:
            return "Undefined input_type: {0}".format(request.form['input_type'])

    # If ve have empty var array or other errors we need to catch it and show
    try:
        rendered_jinja2_tpl = jinja2_tpl.render(values)
    except (exceptions.TemplateRuntimeError, ValueError, TypeError) as e:
        return "Error in your values input filed: {0}".format(e)

    # Replace whitespaces with a visible character (using unicode character)
    if bool(int(request.form['showwhitespaces'])):
        rendered_jinja2_tpl = rendered_jinja2_tpl.replace(' ', u'\u02d9')

    return escape(rendered_jinja2_tpl)


if __name__ == "__main__":
    # Set up logging
    app.logger.setLevel(logging.__getattribute__(config.LOGGING_LEVEL))
    file_handler = logging.handlers.RotatingFileHandler(filename=config.LOGGING_LOCATION, maxBytes=10*1024*1024, backupCount=5)
    file_handler.setFormatter(logging.Formatter(config.LOGGING_FORMAT))
    file_handler.setLevel(logging.__getattribute__(config.LOGGING_LEVEL))
    app.logger.addHandler(file_handler)
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0    # disable caching

    app.run(
        host=config.HOST,
        port=config.PORT,
        debug=config.DEBUG,
    )

