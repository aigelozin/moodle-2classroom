"""Собирает синтетический .mbz (формат Moodle 4.x) для теста."""
import hashlib, io, os, sys, tarfile

out = sys.argv[1]
files = {}
blobs = {}

def add(path, text):
    files[path] = text.encode()

def blob(data: bytes):
    h = hashlib.sha1(data).hexdigest()
    files[f"files/{h[:2]}/{h}"] = data
    return h

pdf = blob(b"%PDF-1.4 fake")
png = blob(b"\x89PNG\r\n\x1a\nfake")

add("moodle_backup.xml", """<?xml version="1.0"?><moodle_backup><information>
<moodle_release>4.1.2 (Build: 20230313)</moodle_release>
<original_course_fullname>Мантра-йога (тест)</original_course_fullname><original_course_shortname>117</original_course_shortname>
<contents><activities>
<activity><moduleid>11</moduleid><sectionid>1</sectionid><modulename>page</modulename><title>Введение</title><directory>activities/page_11</directory></activity>
<activity><moduleid>12</moduleid><sectionid>1</sectionid><modulename>resource</modulename><title>Конспект</title><directory>activities/resource_12</directory></activity>
<activity><moduleid>13</moduleid><sectionid>1</sectionid><modulename>url</modulename><title>Видео</title><directory>activities/url_13</directory></activity>
<activity><moduleid>14</moduleid><sectionid>2</sectionid><modulename>quiz</modulename><title>Тест 1</title><directory>activities/quiz_14</directory></activity>
<activity><moduleid>15</moduleid><sectionid>2</sectionid><modulename>forum</modulename><title>Форум</title><directory>activities/forum_15</directory></activity>
<activity><moduleid>16</moduleid><sectionid>2</sectionid><modulename>label</modulename><title>Метка</title><directory>activities/label_16</directory></activity>
</activities><sections>
<section><sectionid>0</sectionid><title>0</title><directory>sections/section_0</directory></section>
<section><sectionid>1</sectionid><title>1</title><directory>sections/section_1</directory></section>
<section><sectionid>2</sectionid><title>2</title><directory>sections/section_2</directory></section>
</sections></contents></information></moodle_backup>""")
add("course/course.xml", '<course id="5"><shortname>117</shortname><fullname>Мантра-йога (тест)</fullname><summary>&lt;p&gt;Курс о &lt;b&gt;мантрах&lt;/b&gt;.&lt;/p&gt;</summary></course>')
add("sections/section_0/section.xml", '<section id="0"><number>0</number><name></name><summary></summary><sequence></sequence><visible>1</visible></section>')
add("sections/section_1/section.xml", '<section id="1"><number>1</number><name>Основы</name><summary>&lt;p&gt;Первый блок&lt;/p&gt;</summary><sequence>11,12,13</sequence><visible>1</visible></section>')
add("sections/section_2/section.xml", '<section id="2"><number>2</number><name>Проверка</name><summary></summary><sequence>16,14,15</sequence><visible>1</visible></section>')

def mod(n, name, cm, ctx, body, visible=1):
    add(f"activities/{name}_{cm}/module.xml", f'<module id="{cm}"><modulename>{name}</modulename><visible>{visible}</visible></module>')
    add(f"activities/{name}_{cm}/{name}.xml", f'<activity id="{n}" moduleid="{cm}" modulename="{name}" contextid="{ctx}"><{name} id="{n}">{body}</{name}></activity>')

mod(1, "page", 11, 101, "<name>Введение</name><intro></intro><content>&lt;p&gt;Текст &lt;img src=\"@@PLUGINFILE@@/om.png\" alt=\"ом\"&gt; и &lt;a href=\"https://youtu.be/dQw4w9WgXcQ\"&gt;видео&lt;/a&gt;&lt;/p&gt;</content>")
mod(2, "resource", 12, 102, "<name>Конспект</name><intro>&lt;p&gt;PDF лекции&lt;/p&gt;</intro>")
mod(3, "url", 13, 103, "<name>Видео</name><intro></intro><externalurl>https://www.youtube.com/watch?v=abcdefghijk</externalurl>")
mod(5, "forum", 15, 105, "<name>Форум</name><intro></intro>")
mod(6, "label", 16, 106, "<name>м</name><intro>&lt;h3&gt;Перед тестом&lt;/h3&gt;&lt;p&gt;Повторите материал&lt;/p&gt;</intro>", visible=0)
mod(4, "quiz", 14, 104, """<name>Тест 1</name><intro>&lt;p&gt;10 минут&lt;/p&gt;</intro><grade>10</grade><sumgrades>5</sumgrades>
<question_instances>
<question_instance id="1"><slot>1</slot><page>1</page><maxmark>1</maxmark><question_reference><questionbankentryid>501</questionbankentryid><version></version></question_reference></question_instance>
<question_instance id="2"><slot>2</slot><page>1</page><maxmark>1.5</maxmark><question_reference><questionbankentryid>502</questionbankentryid></question_reference></question_instance>
<question_instance id="3"><slot>3</slot><page>2</page><maxmark>1</maxmark><question_reference><questionbankentryid>503</questionbankentryid></question_reference></question_instance>
<question_instance id="4"><slot>4</slot><page>2</page><maxmark>2</maxmark><question_reference><questionbankentryid>504</questionbankentryid></question_reference></question_instance>
<question_instance id="5"><slot>5</slot><page>2</page><maxmark>1</maxmark><question_set_reference><questionscontextid>1</questionscontextid></question_set_reference></question_instance>
<question_instance id="6"><slot>6</slot><page>3</page><maxmark>3</maxmark><question_reference><questionbankentryid>505</questionbankentryid></question_reference></question_instance>
</question_instances>""")

def q(entry, qid, qtype, text, plugin, ver=1, extra_old=""):
    return f"""<question_bank_entry id="{entry}"><question_version><question_versions id="{qid}"><version>{ver}</version><questions>
<question id="{qid}"><name>Q{qid}</name><questiontext>{text}</questiontext><generalfeedback>&lt;p&gt;См. лекцию 1&lt;/p&gt;</generalfeedback><defaultmark>1</defaultmark><qtype>{qtype}</qtype>{plugin}</question>
</questions></question_versions></question_version></question_bank_entry>"""

def answers(*a):
    return "<answers>" + "".join(f'<answer id="{i}"><answertext>{t}</answertext><answerformat>1</answerformat><fraction>{f}</fraction><feedback></feedback></answer>' for i, (t, f) in enumerate(a)) + "</answers>"

mc = f"<plugin_qtype_multichoice_question>{answers(('&lt;p&gt;Ом&lt;/p&gt;', '1.0000000'), ('Хам', '0'), ('Сам', '0'))}<multichoice id='1'><single>1</single></multichoice></plugin_qtype_multichoice_question>"
mc_multi = f"<plugin_qtype_multichoice_question>{answers(('Гаятри', '0.5'), ('Махамритьюнджая', '0.5'), ('Йога-сутра', '-0.5'))}<multichoice id='2'><single>0</single></multichoice></plugin_qtype_multichoice_question>"
tf = f"<plugin_qtype_truefalse_question>{answers(('True', '1'), ('False', '0'))}</plugin_qtype_truefalse_question>"
sa = f"<plugin_qtype_shortanswer_question>{answers(('108', '1'), ('сто восемь', '1'))}</plugin_qtype_shortanswer_question>"
match = "<plugin_qtype_match_question><matchoptions id='1'><shuffleanswers>1</shuffleanswers></matchoptions><matches><match id='1'><questiontext>&lt;p&gt;Ом&lt;/p&gt;</questiontext><answertext>Аджна</answertext></match><match id='2'><questiontext>Лам</questiontext><answertext>Муладхара</answertext></match><match id='3'><questiontext>Вам</questiontext><answertext>Свадхистхана</answertext></match></matches></plugin_qtype_match_question>"
add("questions.xml", "<question_categories><question_category id='1'><question_bank_entries>"
    + q(501, 9001, "multichoice", "&lt;p&gt;Главная мантра?&lt;/p&gt;&lt;img src='@@PLUGINFILE@@/x.png'&gt;", mc)
    + q(502, 9002, "multichoice", "Выберите мантры", mc_multi)
    + q(503, 9003, "truefalse", "Мантру повторяют 108 раз", tf)
    + q(504, 9004, "shortanswer", "Сколько бусин в мале?", sa)
    + q(505, 9005, "match", "Сопоставьте биджа-мантры и чакры", match)
    + "</question_bank_entries></question_category></question_categories>")

add("files.xml", f"""<files>
<file id="1"><contenthash>{pdf}</contenthash><contextid>102</contextid><component>mod_resource</component><filearea>content</filearea><itemid>0</itemid><filepath>/</filepath><filename>lecture1.pdf</filename><filesize>13</filesize><mimetype>application/pdf</mimetype></file>
<file id="2"><contenthash>{png}</contenthash><contextid>101</contextid><component>mod_page</component><filearea>content</filearea><itemid>0</itemid><filepath>/</filepath><filename>om.png</filename><filesize>12</filesize><mimetype>image/png</mimetype></file>
<file id="3"><contenthash>da39a3ee5e6b4b0d3255bfef95601890afd80709</contenthash><contextid>102</contextid><component>mod_resource</component><filearea>content</filearea><itemid>0</itemid><filepath>/</filepath><filename>.</filename><filesize>0</filesize><mimetype>$@NULL@$</mimetype></file>
</files>""")

with tarfile.open(out, "w:gz") as t:
    for p, data in files.items():
        ti = tarfile.TarInfo(p); ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
print("ok", out)
