import math, operator, os, re, requests
from datetime import datetime
from modules import plex, ergast, util
from modules.util import Failed, NotScheduled, YAML
from plexapi.exceptions import NotFound, BadRequest

logger = util.logger

all_auto = ["genre", "number", "custom"]
ms_auto = [
    "actor", "year", "content_rating", "original_language", "tmdb_popular_people", "trakt_user_lists", "studio",
    "trakt_liked_lists", "trakt_people_list", "subtitle_language", "audio_language", "resolution", "decade"
]
auto = {
    "Movie": ["tmdb_collection", "edition", "country", "director", "producer", "writer"] + all_auto + ms_auto,
    "Show": ["network", "origin_country"] + all_auto + ms_auto,
    "Artist": ["mood", "style", "country"] + all_auto,
    "Video": ["country", "content_rating"] + all_auto
}
dynamic_attributes = [
    "type", "data", "exclude", "addons", "template", "template_variables", "other_template", "remove_suffix",
    "remove_prefix", "title_format", "key_name_override", "title_override", "test", "sync", "include", "other_name"
]
auto_type_translation = {
    "content_rating": "contentRating", "subtitle_language": "subtitleLanguage", "audio_language": "audioLanguage",
    "album_style": "album.style", "edition": "editionTitle"
}
default_templates = {
    "original_language": {"plex_all": True, "filters": {"original_language": "<<value>>"}},
    "origin_country": {"plex_all": True, "filters": {"origin_country": "<<value>>"}},
    "tmdb_collection": {"tmdb_collection_details": "<<value>>", "minimum_items": 2},
    "trakt_user_lists": {"trakt_list_details": "<<value>>"},
    "trakt_liked_lists": {"trakt_list_details": "<<value>>"},
    "tmdb_popular_people": {"tmdb_person": "<<value>>", "plex_search": {"all": {"actor": "tmdb"}}},
    "trakt_people_list": {"tmdb_person": "<<value>>", "plex_search": {"all": {"actor": "tmdb"}}}
}

def get_dict(attribute, attr_data, check_list=None, make_str=False):
    if check_list is None:
        check_list = []
    if attr_data and attribute in attr_data:
        if attr_data[attribute]:
            if isinstance(attr_data[attribute], dict):
                new_dict = {}
                for _name, _data in attr_data[attribute].items():
                    if make_str and str(_name) in check_list or not make_str and _name in check_list:
                        new_name = f'"{str(_name)}"' if make_str or not isinstance(_name, int) else _name
                        logger.warning(f"Config Warning: Skipping duplicate {attribute[:-1] if attribute[-1] == 's' else attribute}: {new_name}")
                    elif _data is None:
                        continue
                    elif attribute != "queues" and not isinstance(_data, dict):
                        logger.warning(f"Config Warning: {attribute[:-1] if attribute[-1] == 's' else attribute}: {_name} must be a dictionary")
                    elif attribute == "templates":
                        new_dict[str(_name)] = (_data, {})
                    else:
                        new_dict[str(_name) if make_str else _name] = _data
                return new_dict
            else:
                logger.error(f"Config Error: {attribute} must be a dictionary")
        else:
            logger.error(f"Config Error: {attribute} attribute is blank")
    return {}


class DataFile:
    def __init__(self, config, file_type, path, temp_vars, asset_directory):
        self.config = config
        self.library = None
        self.type = file_type
        self.path = path
        self.temp_vars = temp_vars
        self.asset_directory = asset_directory
        self.data_type = ""
        self.templates = {}
        self.translations = {}
        self.key_names = {}
        self.translation_variables = {}

    def get_file_name(self):
        data = f"{self.config.GitHub.configs_url}{self.path}.yml" if self.type == "GIT" else self.path
        if "/" in data:
            if data.endswith(".yml"):
                return data[data.rfind("/") + 1:-4]
            elif data.endswith(".yaml"):
                return data[data.rfind("/") + 1:-5]
            else:
                return data[data.rfind("/") + 1:]
        elif "\\" in data:
            if data.endswith(".yml"):
                return data[data.rfind("\\") + 1:-4]
            elif data.endswith(".yaml"):
                return data[data.rfind("/") + 1:-5]
            else:
                return data[data.rfind("\\") + 1:]
        else:
            return data

    def load_file(self, file_type, file_path, overlay=False, translation=False):
        if translation:
            if file_path.endswith(".yml"):
                file_path = file_path[:-4]
            elif file_path.endswith(".yaml"):
                file_path = file_path[:-5]
        if not translation and not file_path.endswith((".yml", ".yaml")):
            file_path = f"{file_path}.yml"
        if file_type in ["URL", "Git", "Repo"]:
            if file_type == "Repo" and not self.config.custom_repo:
                raise Failed("Config Error: No custom_repo defined")
            content_path = file_path if file_type == "URL" else f"{self.config.custom_repo if file_type == 'Repo' else self.config.GitHub.configs_url}{file_path}"
            dir_path = content_path
            if translation:
                content_path = f"{content_path}/default.yml"
            response = self.config.get(content_path)
            if response.status_code >= 400:
                raise Failed(f"URL Error: No file found at {content_path}")
            yaml = YAML(input_data=response.content, check_empty=True)
        else:
            if file_type == "PMM Default":
                if not overlay and file_path.startswith(("movie/", "chart/", "award/")):
                    file_path = file_path[6:]
                elif not overlay and file_path.startswith(("show/", "both/")):
                    file_path = file_path[5:]
                elif overlay and file_path.startswith("overlays/"):
                    file_path = file_path[9:]
                defaults_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "defaults")
                if overlay:
                    defaults_path = os.path.join(defaults_path, "overlays")
                if os.path.exists(os.path.abspath(os.path.join(defaults_path, file_path))):
                    file_path = os.path.abspath(os.path.join(defaults_path, file_path))
                elif self.library:
                    for default_folder in [self.library.type.lower(), "both", "chart", "award"]:
                        if os.path.exists(os.path.abspath(os.path.join(defaults_path, default_folder, file_path))):
                            file_path = os.path.abspath(os.path.join(defaults_path, default_folder, file_path))
                            break
            content_path = os.path.abspath(f"{file_path}/default.yml" if translation else file_path)
            dir_path = file_path
            if not os.path.exists(content_path):
                if file_type == "PMM Default":
                    raise Failed(f"File Error: Default does not exist {file_path}")
                else:
                    raise Failed(f"File Error: File does not exist {content_path}")
            yaml = YAML(path=content_path, check_empty=True)
        if not translation:
            logger.debug(f"File Loaded From: {content_path}")
            return yaml.data
        if "translations" not in yaml.data:
            raise Failed(f"URL Error: Top Level translations attribute not found in {content_path}")
        translations = {k: {"default": v} for k, v in yaml.data["translations"].items()}
        lib_type = self.library.type.lower() if self.library else "item"
        logger.debug(f"Translations Loaded From: {dir_path}")
        key_names = {}
        variables = {k: {"default": v[lib_type]} for k, v in yaml.data["variables"].items()}

        def add_translation(yaml_path, yaml_key, data=None):
            yaml_content = YAML(input_data=data, path=yaml_path if data is None else None, check_empty=True)
            if "variables" in yaml_content.data and yaml_content.data["variables"]:
                for var_key, var_value in yaml_content.data["variables"].items():
                    if lib_type in var_value:
                        if var_key not in variables:
                            variables[var_key] = {}
                        variables[var_key][yaml_key] = var_value[lib_type]

            if "translations" in yaml_content.data and yaml_content.data["translations"]:
                for ky, vy in yaml_content.data["translations"].items():
                    if ky in translations:
                        translations[ky][yaml_key] = vy
                    else:
                        logger.error(f"Config Error: {ky} must have a default value in {yaml_path}")
            else:
                logger.error(f"Config Error: Top Level translations attribute not found in {yaml_path}")
            if "key_names" in yaml_content.data and yaml_content.data["key_names"]:
                for kn, vn in yaml_content.data["key_names"].items():
                    if kn not in key_names:
                        key_names[kn] = {}
                    key_names[kn][yaml_key] = vn

        if file_type in ["URL", "Git", "Repo"]:
            if "languages" in yaml.data and isinstance(yaml.data["language"], list):
                for language in yaml.data["language"]:
                    response = self.config.get(f"{dir_path}/{language}.yml")
                    if response.status_code < 400:
                        add_translation(f"{dir_path}/{language}.yml", language, data=response.content)
                    else:
                        logger.error(f"URL Error: Language file not found at {dir_path}/{language}.yml")
        else:
            for file in os.listdir(dir_path):
                if file.endswith(".yml") and file != "default.yml":
                    add_translation(os.path.abspath(f"{dir_path}/{file}"), file[:-4])
        return translations, key_names, variables

    def apply_template(self, call_name, mapping_name, data, template_call, extra_variables):
        if not self.templates:
            raise Failed(f"{self.data_type} Error: No templates found")
        elif not template_call:
            raise Failed(f"{self.data_type} Error: template attribute is blank")
        else:
            new_attributes = {}
            for variables in util.get_list(template_call, split=False):
                if not isinstance(variables, dict):
                    raise Failed(f"{self.data_type} Error: template attribute is not a dictionary")
                elif "name" not in variables:
                    raise Failed(f"{self.data_type} Error: template sub-attribute name is required")
                elif not variables["name"]:
                    raise Failed(f"{self.data_type} Error: template sub-attribute name is blank")
                elif variables["name"] not in self.templates:
                    raise Failed(f"{self.data_type} Error: template {variables['name']} not found")
                elif not isinstance(self.templates[variables["name"]][0], dict):
                    raise Failed(f"{self.data_type} Error: template {variables['name']} is not a dictionary")
                else:
                    logger.separator(f"Template {variables['name']}", space=False, border=False, debug=True)
                    logger.trace("")
                    logger.trace(f"Call: {variables}")

                    remove_variables = []
                    optional = []
                    for tm in variables:
                        if variables[tm] is None:
                            remove_variables.append(tm)
                            variables.pop(tm)
                            optional.append(str(tm))

                    template, temp_vars = self.templates[variables["name"]]

                    if call_name:
                        name = call_name
                    elif "name" in template:
                        name = template["name"]
                    else:
                        name = mapping_name

                    name_var = f"{self.data_type.lower()}_name"
                    variables[name_var] = str(name)
                    variables["mapping_name"] = mapping_name
                    variables["library_type"] = self.library.type.lower() if self.library else "item"
                    variables["library_typeU"] = self.library.type if self.library else "Item"
                    variables["library_name"] = self.library.name if self.library else "playlist"

                    conditionals = {}
                    if "conditionals" in template:
                        if not template["conditionals"]:
                            raise Failed(f"{self.data_type} Error: template sub-attribute conditionals is blank")
                        if not isinstance(template["conditionals"], dict):
                            raise Failed(f"{self.data_type} Error: template sub-attribute conditionals is not a dictionary")
                        for ck, cv in template["conditionals"].items():
                            conditionals[ck] = cv

                    added_vars = {}
                    init_defaults = {}
                    if "default" in template:
                        if not template["default"]:
                            raise Failed(f"{self.data_type} Error: template sub-attribute default is blank")
                        if not isinstance(template["default"], dict):
                            raise Failed(f"{self.data_type} Error: template sub-attribute default is not a dictionary")
                        init_defaults = template["default"]
                    all_init_defaults = {k: v for k, v in init_defaults.items()}

                    temp_conditionals = {}
                    for input_dict, input_type, overwrite_call in [
                        (temp_vars, "External", False),
                        (extra_variables, "Definition", False),
                        (self.temp_vars, "Config", True)
                    ]:
                        logger.trace("")
                        logger.trace(f"{input_type}: {input_dict}")
                        for input_key, input_value in input_dict.items():
                            if input_key == "conditionals":
                                if not input_value:
                                    raise Failed(f"{self.data_type} Error: {input_type} template sub-attribute conditionals is blank")
                                if not isinstance(input_value, dict):
                                    raise Failed(f"{self.data_type} Error: {input_type} template sub-attribute conditionals is not a dictionary")
                                for ck, cv in input_value.items():
                                    temp_conditionals[ck] = cv
                            elif input_key == "default":
                                if not input_value:
                                    raise Failed(f"{self.data_type} Error: {input_type} template sub-attribute default is blank")
                                if not isinstance(input_value, dict):
                                    raise Failed(f"{self.data_type} Error: {input_type} template sub-attribute default is not a dictionary")
                                for dk, dv in input_value.items():
                                    all_init_defaults[dk] = dv
                            elif input_value is None:
                                optional.append(str(input_key))
                                if input_key in variables:
                                    variables.pop(input_key)
                                if input_key in added_vars:
                                    added_vars.pop(input_key)
                            elif overwrite_call:
                                variables[input_key] = input_value
                            else:
                                added_vars[input_key] = input_value
                    for k, v in added_vars.items():
                        if k not in variables:
                            variables[k] = v
                    for k, v in temp_conditionals.items():
                        if k not in variables:
                            conditionals[k] = v

                    language = variables["language"] if "language" in variables else "default"
                    translation_variables = {k: v[language if language in v else "default"] for k, v in self.translations.items()}
                    translation_variables.update({k: v[language if language in v else "default"] for k, v in self.translation_variables.items() if language in v or "default" in v})
                    key_name_variables = {}
                    for var_key, var_value in self.key_names.items():
                        if var_key == "library_type" and language in var_value:
                            variables[var_key] = var_value[language].lower()
                            variables[f"{var_key}U"] = var_value[language]
                        elif language in var_value:
                            key_name_variables[var_key] = var_value[language]
                    if "key_name" in variables:
                        variables["original_key_name"] = variables["key_name"]
                        first_letter = str(variables["key_name"]).upper()[0]
                        variables["key_name_first_letter"] = first_letter if first_letter.isalpha() else "#"
                        if variables["key_name"] in key_name_variables:
                            variables["key_name"] = key_name_variables[variables["key_name"]]
                        variables["translated_key_name"] = variables["key_name"]

                    def replace_var(input_item, search_dicts):
                        if not isinstance(search_dicts, list):
                            search_dicts = [search_dicts]
                        return_item = input_item
                        for search_dict in search_dicts:
                            for rk, rv in search_dict.items():
                                if f"<<{rk}>>" == str(return_item):
                                    return_item = rv
                                if f"<<{rk}>>" in str(return_item):
                                    return_item = str(return_item).replace(f"<<{rk}>>", str(rv))
                        return return_item

                    default = {}
                    if all_init_defaults:
                        var_default = {replace_var(dk, variables): replace_var(dv, variables) for dk, dv in all_init_defaults.items() if dk not in variables}
                        for dkey, dvalue in var_default.items():
                            final_key = replace_var(dkey, var_default)
                            final_value = replace_var(dvalue, var_default)
                            if final_key not in optional and final_key not in variables and final_key not in conditionals:
                                default[final_key] = final_value
                                default[f"{final_key}_encoded"] = requests.utils.quote(str(final_value))

                    if "optional" in template:
                        if template["optional"]:
                            for op in util.get_list(template["optional"]):
                                op = replace_var(op, variables)
                                if op not in default and op not in conditionals:
                                    optional.append(str(op))
                                    optional.append(f"{op}_encoded")
                                elif op in init_defaults:
                                    logger.debug("")
                                    logger.debug(f"Template Warning: variable {op} cannot be optional if it has a default")
                        else:
                            raise Failed(f"{self.data_type} Error: template sub-attribute optional is blank")

                    for con_key, con_value in conditionals.items():
                        logger.debug("")
                        logger.debug(f"Conditional: {con_key}")
                        if not isinstance(con_value, dict):
                            raise Failed(f"{self.data_type} Error: conditional {con_key} is not a dictionary")
                        final_key = replace_var(con_key, [variables, default])
                        if final_key != con_key:
                            logger.trace(f"Variable: {final_key}")
                        if final_key in variables:
                            logger.debug(f'Conditional Variable: {final_key} overwritten to "{variables[final_key]}"')
                            continue
                        if "conditions" not in con_value:
                            raise Failed(f"{self.data_type} Error: conditions sub-attribute required")
                        conditions = con_value["conditions"]
                        if isinstance(conditions, dict):
                            conditions = [conditions]
                        if not isinstance(conditions, list):
                            raise Failed(f"{self.data_type} Error: conditions sub-attribute must be a list or dictionary")
                        condition_found = False
                        for i, condition in enumerate(conditions, 1):
                            if not isinstance(condition, dict):
                                raise Failed(f"{self.data_type} Error: each condition must be a dictionary")
                            if "value" not in condition:
                                raise Failed(f"{self.data_type} Error: each condition must have a result value")
                            condition_passed = True
                            for var_key, var_value in condition.items():
                                if var_key == "value":
                                    continue
                                var_key = replace_var(var_key, [variables, default])
                                var_value = replace_var(var_value, [variables, default])
                                if var_key.endswith(".exists"):
                                    var_value = util.parse(self.data_type, var_key, var_value, datatype="bool", default=False)
                                    if (not var_value and var_key[:-7] in variables and variables[var_key[:-7]]) or (var_value and (var_key[:-7] not in variables or not variables[var_key[:-7]])):
                                        logger.trace(f"Condition {i} Failed: {var_key}: {'true does not exist' if var_value else 'false exists'}")
                                        condition_passed = False
                                elif var_key.endswith(".not"):
                                    if (isinstance(var_value, list) and variables[var_key] in var_value) or \
                                            (not isinstance(var_value, list) and str(variables[var_key]) == str(var_value)):
                                        if isinstance(var_value, list):
                                            logger.trace(f'Condition {i} Failed: {var_key} "{variables[var_key]}" in {var_value}')
                                        else:
                                            logger.trace(f'Condition {i} Failed: {var_key} "{variables[var_key]}" is "{var_value}"')
                                        condition_passed = False
                                elif var_key in variables:
                                    if (isinstance(var_value, list) and variables[var_key] not in var_value) or \
                                            (not isinstance(var_value, list) and str(variables[var_key]) != str(var_value)):
                                        if isinstance(var_value, list):
                                            logger.trace(f'Condition {i} Failed: {var_key} "{variables[var_key]}" not in {var_value}')
                                        else:
                                            logger.trace(f'Condition {i} Failed: {var_key} "{variables[var_key]}" is not "{var_value}"')
                                        condition_passed = False
                                elif var_key in default:
                                    if (isinstance(var_value, list) and default[var_key] not in var_value) or \
                                            (not isinstance(var_value, list) and str(default[var_key]) != str(var_value)):
                                        if isinstance(var_value, list):
                                            logger.trace(f'Condition {i} Failed: {var_key} "{default[var_key]}" not in {var_value}')
                                        else:
                                            logger.trace(f'Condition {i} Failed: {var_key} "{default[var_key]}" is not "{var_value}"')
                                        condition_passed = False
                                else:
                                    logger.trace(f"Condition {i} Failed: {var_key} is not a variable provided or a default variable")
                                    condition_passed = False
                            if condition_passed:
                                logger.debug(f'Conditional Variable: {final_key} is "{condition["value"]}"')
                                condition_found = True
                                variables[final_key] = condition["value"]
                                variables[f"{final_key}_encoded"] = requests.utils.quote(str(condition["value"]))
                                break
                        if not condition_found:
                            if "default" in con_value:
                                logger.debug(f'Conditional Variable: {final_key} defaults to "{con_value["default"]}"')
                                variables[final_key] = con_value["default"]
                                variables[f"{final_key}_encoded"] = requests.utils.quote(str(con_value["default"]))
                            else:
                                logger.debug(f"Conditional Variable: {final_key} added as optional variable")
                                optional.append(str(final_key))
                                optional.append(f"{final_key}_encoded")

                    sort_name = None
                    if "move_prefix" in template or "move_collection_prefix" in template:
                        prefix = None
                        if "move_prefix" in template:
                            prefix = template["move_prefix"]
                        elif "move_collection_prefix" in template:
                            logger.debug("")
                            logger.debug(f"{self.data_type} Warning: template sub-attribute move_collection_prefix will run as move_prefix")
                            prefix = template["move_collection_prefix"]
                        if prefix:
                            for op in util.get_list(prefix):
                                if variables[name_var].startswith(f"{op} "):
                                    sort_name = f"{variables[name_var][len(op):].strip()}, {op}"
                                    break
                        else:
                            raise Failed(f"{self.data_type} Error: template sub-attribute move_prefix is blank")
                    variables[f"{self.data_type.lower()}_sort"] = sort_name if sort_name else variables[name_var]

                    for key, value in variables.copy().items():
                        if "<<" in key and ">>" in key:
                            for k, v in variables.items():
                                if f"<<{k}>>" in key:
                                    key = key.replace(f"<<{k}>>", v)
                            for k, v in default.items():
                                if f"<<{k}>>" in key:
                                    key = key.replace(f"<<{k}>>", v)
                            variables[key] = value
                    for key, value in variables.copy().items():
                        variables[f"{key}_encoded"] = requests.utils.quote(str(value))

                    default = {k: v for k, v in default.items() if k not in variables}
                    optional = [o for o in optional if o not in variables and o not in default]

                    logger.trace("")
                    logger.trace(f"Variables: {variables}")
                    logger.trace("")
                    logger.trace(f"Defaults: {default}")
                    logger.trace("")
                    logger.trace(f"Optional: {optional}")
                    logger.trace("")
                    logger.trace(f"Translation: {translation_variables}")
                    logger.debug("")

                    def check_for_var(_method, _data):
                        def scan_text(og_txt, var, actual_value):
                            if og_txt is None:
                                return og_txt
                            elif str(og_txt) == f"<<{var}>>":
                                return actual_value
                            elif f"<<{var}>>" in str(og_txt):
                                return str(og_txt).replace(f"<<{var}>>", str(actual_value))
                            else:
                                return og_txt
                        for i_check in range(8):
                            for option in optional:
                                if option not in variables and option not in translation_variables and f"<<{option}>>" in str(_data):
                                    raise Failed
                            for variable, variable_data in variables.items():
                                if (variable == "collection_name" or variable == "playlist_name") and _method in ["radarr_tag", "item_radarr_tag", "sonarr_tag", "item_sonarr_tag"]:
                                    _data = scan_text(_data, variable, variable_data.replace(",", ""))
                                elif variable != "name":
                                    _data = scan_text(_data, variable, variable_data)
                            for variable, variable_data in translation_variables.items():
                                _data = scan_text(_data, variable, variable_data)
                            for dm, dd in default.items():
                                _data = scan_text(_data, dm, dd)
                        return _data

                    def check_data(_method, _data):
                        if isinstance(_data, dict):
                            final_data = {}
                            for sm, sd in _data.items():
                                try:
                                    final_data[check_for_var(_method, sm)] = check_data(_method, sd)
                                except Failed:
                                    continue
                            if not final_data:
                                raise Failed
                        elif isinstance(_data, list):
                            final_data = []
                            for li in _data:
                                try:
                                    final_data.append(check_data(_method, li))
                                except Failed:
                                    continue
                            if not final_data:
                                raise Failed
                        else:
                            final_data = check_for_var(_method, _data)
                        return final_data

                    for method_name, attr_data in template.items():
                        if method_name not in data and method_name not in ["default", "optional", "conditionals", "move_collection_prefix", "move_prefix"]:
                            try:
                                new_name = check_for_var(method_name, method_name)
                                if new_name in new_attributes:
                                    logger.info("")
                                    logger.warning(f"Template Warning: template attribute: {new_name} from {variables['name']} skipped")
                                else:
                                    new_attributes[new_name] = check_data(new_name, attr_data)
                            except Failed:
                                continue
            logger.debug("")
            logger.separator(f"Final Template Attributes", space=False, border=False, debug=True)
            logger.debug("")
            logger.debug(new_attributes)
            logger.debug("")
            return new_attributes

    def external_templates(self, data, overlay=False):
        if data and "external_templates" in data and data["external_templates"]:
            files = util.load_files(data["external_templates"], "external_templates")
            if not files:
                logger.error("Config Error: No Paths Found for external_templates")
            for file_type, template_file, temp_vars, _ in files:
                temp_data = self.load_file(file_type, template_file, overlay=overlay)
                if temp_data and isinstance(temp_data, dict) and "templates" in temp_data and temp_data["templates"] and isinstance(temp_data["templates"], dict):
                    self.templates.update({k: (v, temp_vars) for k, v in temp_data["templates"].items() if k not in self.templates})

    def translation_files(self, data, overlay=False):
        if data and "translations" in data and data["translations"]:
            files = util.load_files(data["translations"], "translations")
            if not files:
                logger.error("Config Error: No Paths Found for translations")
            for file_type, template_file, _, _ in files:
                temp_data, key_data, variables = self.load_file(file_type, template_file, overlay=overlay, translation=True)
                self.translations.update({k: v for k, v in temp_data.items() if k not in self.translations})
                self.key_names.update({k: v for k, v in key_data.items() if k not in self.key_names})
                self.translation_variables.update({k: v for k, v in variables.items() if k not in self.translation_variables})

class MetadataFile(DataFile):
    def __init__(self, config, library, file_type, path, temp_vars, asset_directory):
        super().__init__(config, file_type, path, temp_vars, asset_directory)
        metadata_name = self.get_file_name()
        if config.requested_metadata_files and metadata_name not in config.requested_metadata_files:
            raise NotScheduled(metadata_name)
        self.data_type = "Collection"
        self.library = library
        if file_type == "Data":
            self.metadata = None
            self.collections = get_dict("collections", path, library.collections)
            self.templates = get_dict("templates", path)
        else:
            logger.info("")
            logger.separator(f"Loading Metadata {file_type}: {path}")
            logger.debug("")
            data = self.load_file(self.type, self.path)
            self.metadata = get_dict("metadata", data, library.metadatas)
            self.templates = get_dict("templates", data)
            self.external_templates(data)
            self.translation_files(data)
            self.collections = get_dict("collections", data, library.collections)
            self.dynamic_collections = get_dict("dynamic_collections", data)
            col_names = library.collections + [c for c in self.collections]
            for map_name, dynamic in self.dynamic_collections.items():
                logger.info("")
                logger.separator(f"Building {map_name} Dynamic Collections", space=False, border=False)
                logger.info("")
                try:
                    methods = {dm.lower(): dm for dm in dynamic}
                    for m in methods:
                        if m not in dynamic_attributes:
                            logger.warning(f"Config Warning: {methods[m]} attribute is invalid. Options: {', '.join(dynamic_attributes)}")
                    if "type" not in methods:
                        raise Failed(f"Config Error: {map_name} type attribute not found")
                    elif not dynamic[methods["type"]]:
                        raise Failed(f"Config Error: {map_name} type attribute is blank")
                    elif dynamic[methods["type"]].lower() not in auto[library.type]:
                        raise Failed(f"Config Error: {map_name} type attribute {dynamic[methods['type']].lower()} invalid Options: {auto[library.type]}")
                    elif dynamic[methods["type"]].lower() == "network" and library.agent not in plex.new_plex_agents:
                        raise Failed(f"Config Error: {map_name} type attribute: network only works with the New Plex TV Agent")
                    elif dynamic[methods["type"]].lower().startswith("trakt") and not self.config.Trakt:
                        raise Failed(f"Config Error: {map_name} type attribute: {dynamic[methods['type']]} requires trakt to be configured")
                    auto_type = dynamic[methods["type"]].lower()
                    og_exclude = []
                    if "exclude" in self.temp_vars:
                        og_exclude = util.parse("Config", "exclude", self.temp_vars["exclude"], parent="template_variable", datatype="strlist")
                    elif "exclude" in methods:
                        og_exclude = util.parse("Config", "exclude", dynamic, parent=map_name, methods=methods, datatype="strlist")
                    if "append_exclude" in self.temp_vars:
                        og_exclude.extend(util.parse("Config", "append_exclude", self.temp_vars["append_exclude"], parent="template_variable", datatype="strlist"))
                    include = []
                    if "include" in self.temp_vars:
                        include = util.parse("Config", "include", self.temp_vars["include"], parent="template_variable", datatype="strlist")
                    elif "include" in methods:
                        include = [i for i in util.parse("Config", "include", dynamic, parent=map_name, methods=methods, datatype="strlist") if i not in og_exclude]
                    if "append_include" in self.temp_vars:
                        include.extend(util.parse("Config", "append_include", self.temp_vars["append_include"], parent="template_variable", datatype="strlist"))
                    addons = {}
                    if "addons" in self.temp_vars:
                        addons = util.parse("Config", "addons", self.temp_vars["addons"], parent="template_variable", datatype="dictliststr")
                    elif "addons" in methods:
                        addons = util.parse("Config", "addons", dynamic, parent=map_name, methods=methods, datatype="dictliststr")
                    if "append_addons" in self.temp_vars:
                        append_addons = util.parse("Config", "append_addons", self.temp_vars["append_addons"], parent=map_name, methods=methods, datatype="dictliststr")
                        for k, v in append_addons.items():
                            if k not in addons:
                                addons[k] = []
                            addons[k].extend(v)
                    exclude = [str(e) for e in og_exclude]
                    for k, v in addons.items():
                        if k in v:
                            logger.warning(f"Config Warning: {k} cannot be an addon for itself")
                        exclude.extend([y for y in v if y != k and y not in exclude])
                    default_title_format = "<<key_name>>"
                    default_template = None
                    auto_list = {}
                    all_keys = {}
                    dynamic_data = None
                    def _check_dict(check_dict):
                        for ck, cv in check_dict.items():
                            all_keys[str(ck)] = cv
                            if str(ck) not in exclude and str(cv) not in exclude:
                                auto_list[str(ck)] = cv
                    if auto_type == "decade" and library.is_show:
                        all_items = library.get_all()
                        if addons:
                            raise Failed(f"Config Error: addons cannot be used with show decades")
                        addons = {}
                        all_keys = {}
                        for i, item in enumerate(all_items, 1):
                            logger.ghost(f"Processing: {i}/{len(all_items)} {item.title}")
                            if item.year:
                                decade = str(int(math.floor(item.year / 10) * 10))
                                if decade not in addons:
                                    addons[decade] = []
                                if str(item.year) not in addons[decade]:
                                    addons[decade].append(str(item.year))
                                    all_keys[str(item.year)] = str(item.year)
                        auto_list = {str(k): f"{k}s" for k in addons if str(k) not in exclude and f"{k}s" not in exclude}
                        default_template = {"smart_filter": {"limit": 50, "sort_by": "critic_rating.desc", "any": {"year": "<<value>>"}}}
                        default_title_format = "Best <<library_type>>s of <<key_name>>"
                    elif auto_type in ["genre", "mood", "style", "album_style", "country", "studio", "edition", "network", "year", "decade", "content_rating", "subtitle_language", "audio_language", "resolution"]:
                        search_tag = auto_type_translation[auto_type] if auto_type in auto_type_translation else auto_type
                        if library.is_show and auto_type in ["resolution", "subtitle_language", "audio_language"]:
                            tags = library.get_tags(f"episode.{search_tag}")
                        else:
                            tags = library.get_tags(search_tag)
                        if auto_type in ["subtitle_language", "audio_language"]:
                            all_keys = {}
                            auto_list = {}
                            for i in tags:
                                final_title = self.config.TMDb.TMDb._iso_639_1[str(i.key)].english_name if str(i.key) in self.config.TMDb.TMDb._iso_639_1 else str(i.title)
                                all_keys[str(i.key)] = final_title
                                if all([x not in exclude for x in [final_title, str(i.title), str(i.key)]]):
                                    auto_list[str(i.key)] = final_title
                        elif auto_type in ["resolution", "decade"]:
                            all_keys = {str(i.key): i.title for i in tags}
                            auto_list = {str(i.key): i.title for i in tags if str(i.title) not in exclude and str(i.key) not in exclude}
                        else:
                            all_keys = {str(i.title): i.title for i in tags}
                            auto_list = {str(i.title): i.title for i in tags if str(i.title) not in exclude}
                        if library.is_music:
                            final_var = auto_type if auto_type.startswith("album") else f"artist_{auto_type}"
                            default_template = {"smart_filter": {"limit": 50, "sort_by": "plays.desc", "any": {final_var: "<<value>>"}}}
                            if auto_type.startswith("album"):
                                default_template["builder_level"] = "album"
                            default_title_format = f"Most Played <<key_name>> {'Albums' if auto_type.startswith('album') else '<<library_type>>'}s"
                        elif auto_type == "resolution":
                            default_template = {"smart_filter": {"sort_by": "title.asc", "any": {auto_type: "<<value>>"}}}
                            default_title_format = "<<key_name>> <<library_type>>s"
                        else:
                            default_template = {"smart_filter": {"limit": 50, "sort_by": "critic_rating.desc", "any": {f"{auto_type}.is" if auto_type == "studio" else auto_type: "<<value>>"}}}
                            default_title_format = "Best <<library_type>>s of <<key_name>>" if auto_type in ["year", "decade"] else "Top <<key_name>> <<library_type>>s"
                    elif auto_type == "tmdb_collection":
                        all_items = library.get_all()
                        for i, item in enumerate(all_items, 1):
                            logger.ghost(f"Processing: {i}/{len(all_items)} {item.title}")
                            tmdb_id, tvdb_id, imdb_id = library.get_ids(item)
                            tmdb_item = config.TMDb.get_item(item, tmdb_id, tvdb_id, imdb_id, is_movie=True)
                            if tmdb_item and tmdb_item.collection_id and tmdb_item.collection_name:
                                all_keys[str(tmdb_item.collection_id)] = tmdb_item.collection_name
                                if str(tmdb_item.collection_id) not in exclude and tmdb_item.collection_name not in exclude:
                                    auto_list[str(tmdb_item.collection_id)] = tmdb_item.collection_name
                        logger.exorcise()
                    elif auto_type == "original_language":
                        all_items = library.get_all()
                        for i, item in enumerate(all_items, 1):
                            logger.ghost(f"Processing: {i}/{len(all_items)} {item.title}")
                            tmdb_id, tvdb_id, imdb_id = library.get_ids(item)
                            tmdb_item = config.TMDb.get_item(item, tmdb_id, tvdb_id, imdb_id, is_movie=library.type == "Movie")
                            if tmdb_item and tmdb_item.language_iso:
                                all_keys[tmdb_item.language_iso] = tmdb_item.language_name
                                if tmdb_item.language_iso not in exclude and tmdb_item.language_name not in exclude:
                                    auto_list[tmdb_item.language_iso] = tmdb_item.language_name
                        logger.exorcise()
                        default_title_format = "<<key_name>> <<library_type>>s"
                    elif auto_type == "origin_country":
                        all_items = library.get_all()
                        for i, item in enumerate(all_items, 1):
                            logger.ghost(f"Processing: {i}/{len(all_items)} {item.title}")
                            tmdb_id, tvdb_id, imdb_id = library.get_ids(item)
                            tmdb_item = config.TMDb.get_item(item, tmdb_id, tvdb_id, imdb_id, is_movie=library.type == "Movie")
                            if tmdb_item and tmdb_item.countries:
                                for country in tmdb_item.countries:
                                    all_keys[country.iso_3166_1.lower()] = country.name
                                    if country.iso_3166_1.lower() not in exclude and country.name not in exclude:
                                        auto_list[country.iso_3166_1.lower()] = country.name
                        logger.exorcise()
                        default_title_format = "<<key_name>> <<library_type>>s"
                    elif auto_type in ["actor", "director", "writer", "producer"]:
                        people = {}
                        if "data" not in methods:
                            raise Failed(f"Config Error: {map_name} data attribute not found")
                        elif "data" in self.temp_vars:
                            dynamic_data = util.parse("Config", "data", self.temp_vars["data"], datatype="dict")
                        else:
                            dynamic_data = util.parse("Config", "data", dynamic, parent=map_name, methods=methods, datatype="dict")
                        person_methods = {am.lower(): am for am in dynamic_data}
                        if "actor_depth" in person_methods:
                            person_methods["depth"] = person_methods.pop("actor_depth")
                        if "actor_minimum" in person_methods:
                            person_methods["minimum"] = person_methods.pop("actor_minimum")
                        if "number_of_actors" in person_methods:
                            person_methods["limit"] = person_methods.pop("number_of_actors")
                        person_depth = util.parse("Config", "depth", dynamic_data, parent=f"{map_name} data", methods=person_methods, datatype="int", default=3, minimum=1)
                        person_minimum = util.parse("Config", "minimum", dynamic_data, parent=f"{map_name} data", methods=person_methods, datatype="int", default=3, minimum=1) if "minimum" in person_methods else None
                        person_limit = util.parse("Config", "limit", dynamic_data, parent=f"{map_name} data", methods=person_methods, datatype="int", default=25, minimum=1) if "limit" in person_methods else None
                        lib_all = library.get_all()
                        for i, item in enumerate(lib_all, 1):
                            logger.ghost(f"Scanning: {i}/{len(lib_all)} {item.title}")
                            try:
                                item = self.library.reload(item)
                                for person in getattr(item, f"{auto_type}s")[:person_depth]:
                                    if person.id not in people:
                                        people[person.id] = {"name": person.tag, "count": 0}
                                    people[person.id]["count"] += 1
                            except Failed as e:
                                logger.error(f"Plex Error: {e}")
                        roles = [data for _, data in people.items()]
                        roles.sort(key=operator.itemgetter('count'), reverse=True)
                        if not person_minimum:
                            person_minimum = 1 if person_limit else 3
                        if not person_limit:
                            person_limit = len(roles)
                        person_count = 0
                        for role in roles:
                            if person_count < person_limit and role["count"] >= person_minimum and role["name"] not in exclude:
                                auto_list[role["name"]] = role["name"]
                                all_keys[role["name"]] = role["name"]
                                person_count += 1
                        default_template = {"plex_search": {"any": {auto_type: "<<value>>"}}}
                    elif auto_type == "number":
                        if "data" not in methods:
                            raise Failed(f"Config Error: {map_name} data attribute not found")
                        elif "data" in self.temp_vars:
                            dynamic_data = util.parse("Config", "data", self.temp_vars["data"], datatype="dict")
                        else:
                            dynamic_data = util.parse("Config", "data", dynamic, parent=map_name, methods=methods, datatype="dict")
                        number_methods = {nm.lower(): nm for nm in dynamic_data}
                        if "starting" in number_methods and str(dynamic_data[number_methods["starting"]]).startswith("current_year"):
                            year_values = str(dynamic_data[number_methods["starting"]]).split("-")
                            try:
                                starting = datetime.now().year - (0 if len(year_values) == 1 else int(year_values[1].strip()))
                            except ValueError:
                                raise Failed(f"Config Error: starting attribute modifier invalid '{year_values[1]}'")
                        else:
                            starting = util.parse("Config", "starting", dynamic_data, parent=f"{map_name} data", methods=number_methods, datatype="int", default=0, minimum=0)
                        if "ending" in number_methods and str(dynamic_data[number_methods["ending"]]).startswith("current_year"):
                            year_values = str(dynamic_data[number_methods["ending"]]).split("-")
                            try:
                                ending = datetime.now().year - (0 if len(year_values) == 1 else int(year_values[1].strip()))
                            except ValueError:
                                raise Failed(f"Config Error: ending attribute modifier invalid '{year_values[1]}'")
                        else:
                            ending = util.parse("Config", "ending", dynamic_data, parent=f"{map_name} data", methods=number_methods, datatype="int", default=0, minimum=1)
                        increment = util.parse("Config", "increment", dynamic_data, parent=f"{map_name} data", methods=number_methods, datatype="int", default=1, minimum=1) if "increment" in number_methods else 1
                        if starting > ending:
                            raise Failed(f"Config Error: {map_name} data ending must be greater than starting")
                        current = starting
                        while current <= ending:
                            all_keys[str(current)] = str(current)
                            if str(current) not in exclude and current not in exclude:
                                auto_list[str(current)] = str(current)
                            current += increment
                    elif auto_type == "custom":
                        if "data" not in methods:
                            raise Failed(f"Config Error: {map_name} data attribute not found")
                        for k, v in util.parse("Config", "data", dynamic, parent=map_name, methods=methods, datatype="strdict").items():
                            all_keys[k] = v
                            if k not in exclude and v not in exclude:
                                auto_list[k] = v
                    elif auto_type == "trakt_user_lists":
                        dynamic_data = util.parse("Config", "data", dynamic, parent=map_name, methods=methods, datatype="list")
                        for option in dynamic_data:
                            _check_dict({self.config.Trakt.build_user_url(u[0], u[1]): u[2] for u in self.config.Trakt.all_user_lists(option)})
                    elif auto_type == "trakt_liked_lists":
                        _check_dict(self.config.Trakt.all_liked_lists())
                    elif auto_type == "tmdb_popular_people":
                        dynamic_data = util.parse("Config", "data", dynamic, parent=map_name, methods=methods, datatype="int", minimum=1)
                        _check_dict(self.config.TMDb.get_popular_people(dynamic_data))
                    elif auto_type == "trakt_people_list":
                        dynamic_data = util.parse("Config", "data", dynamic, parent=map_name, methods=methods, datatype="list")
                        for option in dynamic_data:
                            _check_dict(self.config.Trakt.get_people(option))
                    else:
                        raise Failed(f"Config Error: {map_name} type attribute {dynamic[methods['type']]} invalid")

                    if "append_data" in self.temp_vars:
                        for k, v in util.parse("Config", "append_data", self.temp_vars["append_data"], parent=map_name, methods=methods, datatype="strdict").items():
                            all_keys[k] = v
                            if k not in exclude and v not in exclude:
                                auto_list[k] = v
                    custom_keys = True
                    if "custom_keys" in self.temp_vars:
                        custom_keys = util.parse("Config", "custom_keys", self.temp_vars["custom_keys"], parent="template_variable", default=custom_keys)
                    elif "custom_keys" in methods:
                        custom_keys = util.parse("Config", "custom_keys", dynamic, parent=map_name, methods=methods, default=custom_keys)
                    for add_key, combined_keys in addons.items():
                        if add_key not in all_keys and add_key not in og_exclude:
                            final_keys = [ck for ck in combined_keys if ck in all_keys]
                            if custom_keys and final_keys:
                                auto_list[add_key] = add_key
                                addons[add_key] = final_keys
                            elif custom_keys:
                                logger.warning(f"Config Warning: {add_key} Custom Key must have at least one Key")
                            else:
                                for final_key in final_keys:
                                    auto_list[final_key] = all_keys[final_key]
                    title_format = default_title_format
                    if "title_format" in self.temp_vars:
                        title_format = util.parse("Config", "title_format", self.temp_vars["title_format"], parent="template_variable", default=default_title_format)
                    elif "title_format" in methods:
                        title_format = util.parse("Config", "title_format", dynamic, parent=map_name, methods=methods, default=default_title_format)
                    if "<<key_name>>" not in title_format and "<<title>>" not in title_format:
                        logger.error(f"Config Error: <<key_name>> not in title_format: {title_format} using default: {default_title_format}")
                        title_format = default_title_format
                    if "post_format_override" in methods:
                        methods["title_override"] = methods.pop("post_format_override")
                    if "pre_format_override" in methods:
                        methods["key_name_override"] = methods.pop("pre_format_override")
                    title_override = util.parse("Config", "title_override", dynamic, parent=map_name, methods=methods, datatype="strdict") if "title_override" in methods else {}
                    key_name_override = util.parse("Config", "key_name_override", dynamic, parent=map_name, methods=methods, datatype="strdict") if "key_name_override" in methods else {}
                    test_override = []
                    for k, v in key_name_override.items():
                        if v in test_override:
                            logger.warning(f"Config Warning: {v} can only be used once skipping {k}: {v}")
                            key_name_override.pop(k)
                        else:
                            test_override.append(v)
                    test = util.parse("Config", "test", dynamic, parent=map_name, methods=methods, default=False, datatype="bool") if "test" in methods else False
                    sync = util.parse("Config", "sync", dynamic, parent=map_name, methods=methods, default=False, datatype="bool") if "sync" in methods else False
                    if "<<library_type>>" in title_format:
                        title_format = title_format.replace("<<library_type>>", library.type.lower())
                    if "<<library_typeU>>" in title_format:
                        title_format = title_format.replace("<<library_typeU>>", library.type)
                    if "limit" in self.temp_vars and "<<limit>>" in title_format:
                        title_format = title_format.replace("<<limit>>", self.temp_vars["limit"])
                    template_variables = util.parse("Config", "template_variables", dynamic, parent=map_name, methods=methods, datatype="dictdict") if "template_variables" in methods else {}
                    if "template" in methods:
                        template_names = util.parse("Config", "template", dynamic, parent=map_name, methods=methods, datatype="strlist")
                        has_var = False
                        for template_name in template_names:
                            if template_name not in self.templates:
                                raise Failed(f"Config Error: {map_name} template: {template_name} not found")
                            if any([a in str(self.templates[template_name][0]) for a in ["<<value>>", "<<key>>", f"<<{auto_type}>>"]]):
                                has_var = True
                        if not has_var:
                            raise Failed(f"Config Error: One {map_name} template: {template_names} is required to have the template variable <<value>>")
                    elif auto_type in ["number", "list"]:
                        raise Failed(f"Config Error: {map_name} template required for type: {auto_type}")
                    else:
                        self.templates[map_name] = (default_template if default_template else default_templates[auto_type], {})
                        template_names = [map_name]
                    remove_prefix = []
                    if "remove_prefix" in self.temp_vars:
                        remove_prefix = util.parse("Config", "remove_prefix", self.temp_vars["remove_prefix"], parent="template_variable", datatype="commalist")
                    elif "remove_prefix" in methods:
                        remove_prefix = util.parse("Config", "remove_prefix", dynamic, parent=map_name, methods=methods, datatype="commalist")
                    remove_suffix = []
                    if "remove_suffix" in self.temp_vars:
                        remove_suffix = util.parse("Config", "remove_suffix", self.temp_vars["remove_suffix"], parent="template_variable", datatype="commalist")
                    elif "remove_suffix" in methods:
                        remove_suffix = util.parse("Config", "remove_suffix", dynamic, parent=map_name, methods=methods, datatype="commalist")
                    sync = {i.title: i for i in self.library.get_all_collections(label=str(map_name))} if sync else {}
                    other_name = None
                    if "other_name" in self.temp_vars and include:
                        other_name = util.parse("Config", "other_name", self.temp_vars["remove_suffix"], parent="template_variable")
                    elif "other_name" in methods and include:
                        other_name = util.parse("Config", "other_name", dynamic, parent=map_name, methods=methods)
                    other_templates = util.parse("Config", "other_template", dynamic, parent=map_name, methods=methods, datatype="strlist") if "other_template" in methods and include else None
                    if other_templates:
                        for other_template in other_templates:
                            if other_template not in self.templates:
                                raise Failed(f"Config Error: {map_name} other template: {other_template} not found")
                    else:
                        other_templates = template_names
                    other_keys = []
                    logger.debug(f"Mapping Name: {map_name}")
                    logger.debug(f"Type: {auto_type}")
                    logger.debug(f"Data: {dynamic_data}")
                    logger.debug(f"Exclude: {og_exclude}")
                    logger.debug(f"Exclude Final: {exclude}")
                    logger.debug(f"Addons: {addons}")
                    logger.debug(f"Template: {template_names}")
                    logger.debug(f"Other Template: {other_templates}")
                    logger.debug(f"Template Variables: {template_variables}")
                    logger.debug(f"Remove Prefix: {remove_prefix}")
                    logger.debug(f"Remove Suffix: {remove_suffix}")
                    logger.debug(f"Title Format: {title_format}")
                    logger.debug(f"Key Name Override: {key_name_override}")
                    logger.debug(f"Title Override: {title_override}")
                    logger.debug(f"Custom Keys: {custom_keys}")
                    logger.debug(f"Test: {test}")
                    logger.debug(f"Sync: {sync}")
                    logger.debug(f"Include: {include}")
                    logger.debug(f"Other Name: {other_name}")
                    logger.debug(f"Keys (Title)")
                    for key, value in auto_list.items():
                        logger.debug(f"  - {key}{'' if key == value else f' ({value})'}")

                    used_keys = []
                    for key, value in auto_list.items():
                        if include and key not in include:
                            if key not in exclude:
                                other_keys.append(key)
                            continue
                        if key in key_name_override:
                            key_name = key_name_override[key]
                        else:
                            key_name = value
                            for prefix in remove_prefix:
                                if key_name.startswith(prefix):
                                    key_name = key_name[len(prefix):].strip()
                            for suffix in remove_suffix:
                                if key_name.endswith(suffix):
                                    key_name = key_name[:-len(suffix)].strip()
                        key_value = [key] if key in all_keys else []
                        if key in addons:
                            key_value.extend([a for a in addons[key] if (a in all_keys or auto_type == "custom") and a != key])
                        used_keys.extend(key_value)
                        og_call = {"value": key_value, auto_type: key_value, "key_name": key_name, "key": key}
                        for k, v in template_variables.items():
                            if key in v:
                                og_call[k] = v[key]
                            elif "default" in v:
                                og_call[k] = v["default"]
                        template_call = []
                        for template_name in template_names:
                            new_call = og_call.copy()
                            new_call["name"] = template_name
                            template_call.append(new_call)
                        if key in title_override:
                            collection_title = title_override[key]
                        else:
                            collection_title = title_format.replace("<<title>>", key_name).replace("<<key_name>>", key_name)
                        if collection_title in col_names:
                            logger.warning(f"Config Warning: Skipping duplicate collection: {collection_title}")
                        else:
                            col = {"template": template_call, "label": str(map_name)}
                            if test:
                                col["test"] = True
                            if collection_title in sync:
                                sync.pop(collection_title)
                            col_names.append(collection_title)
                            self.collections[collection_title] = col
                    if other_name and not other_keys:
                        logger.warning(f"Config Warning: Other Collection {other_name} not needed")
                    elif other_name:
                        og_other = {
                            "value": other_keys, "included_keys": include, "used_keys": used_keys,
                            auto_type: other_keys, "key_name": other_name, "key": "other"
                        }
                        for k, v in template_variables.items():
                            if "other" in v:
                                og_other[k] = v["other"]
                            elif "default" in v:
                                og_other[k] = v["default"]
                        other_call = []
                        for other_template in other_templates:
                            new_call = og_other.copy()
                            new_call["name"] = other_template
                            other_call.append(new_call)
                        col = {"template": other_call, "label": str(map_name)}
                        if test:
                            col["test"] = True
                        if other_name in sync:
                            sync.pop(other_name)
                        self.collections[other_name] = col
                    for col_title, col in sync.items():
                        col.delete()
                        logger.info(f"{map_name} Dynamic Collection: {col_title} Deleted")
                except Failed as e:
                    logger.error(e)
                    logger.error(f"{map_name} Dynamic Collection Failed")
                    continue

            if not self.metadata and not self.collections:
                raise Failed("YAML Error: metadata, collections, or dynamic_collections attribute is required")
            logger.info("")
            logger.info(f"Metadata File Loaded Successfully")

    def get_collections(self, requested_collections):
        if requested_collections:
            return {c: self.collections[c] for c in util.get_list(requested_collections) if c in self.collections}
        else:
            return self.collections

    def edit_tags(self, attr, obj, group, alias, extra=None):
        if attr in alias and f"{attr}.sync" in alias:
            logger.error(f"Metadata Error: Cannot use {attr} and {attr}.sync together")
        elif f"{attr}.remove" in alias and f"{attr}.sync" in alias:
            logger.error(f"Metadata Error: Cannot use {attr}.remove and {attr}.sync together")
        elif attr in alias and not group[alias[attr]]:
            logger.warning(f"Metadata Error: {attr} attribute is blank")
        elif f"{attr}.remove" in alias and not group[alias[f"{attr}.remove"]]:
            logger.warning(f"Metadata Error: {attr}.remove attribute is blank")
        elif f"{attr}.sync" in alias and not group[alias[f"{attr}.sync"]]:
            logger.warning(f"Metadata Error: {attr}.sync attribute is blank")
        elif attr in alias or f"{attr}.remove" in alias or f"{attr}.sync" in alias:
            add_tags = util.get_list(group[alias[attr]]) if attr in alias else []
            if extra:
                add_tags.extend(extra)
            remove_tags = util.get_list(group[alias[f"{attr}.remove"]]) if f"{attr}.remove" in alias else None
            sync_tags = util.get_list(group[alias[f"{attr}.sync"]]) if f"{attr}.sync" in alias else None
            return len(self.library.edit_tags(attr, obj, add_tags=add_tags, remove_tags=remove_tags, sync_tags=sync_tags)) > 0
        return False

    def update_metadata(self):
        if not self.metadata:
            return None
        logger.info("")
        logger.separator("Running Metadata")
        logger.info("")
        next_year = datetime.now().year + 1
        for mapping_name, meta in self.metadata.items():
            methods = {mm.lower(): mm for mm in meta}

            logger.info("")
            if (isinstance(mapping_name, int) or mapping_name.startswith("tt")) and not self.library.is_music:
                if isinstance(mapping_name, int):
                    id_type = "TMDb" if self.library.is_movie else "TVDb"
                else:
                    id_type = "IMDb"
                logger.separator(f"{id_type} ID: {mapping_name} Metadata", space=False, border=False)
                logger.info("")
                item = []
                if self.library.is_movie and mapping_name in self.library.movie_map:
                    for item_id in self.library.movie_map[mapping_name]:
                        item.append(self.library.fetchItem(item_id))
                elif self.library.is_show and mapping_name in self.library.show_map:
                    for item_id in self.library.show_map[mapping_name]:
                        item.append(self.library.fetchItem(item_id))
                elif mapping_name in self.library.imdb_map:
                    for item_id in self.library.imdb_map[mapping_name]:
                        item.append(self.library.fetchItem(item_id))
                else:
                    logger.error(f"Metadata Error: {id_type} ID not mapped")
                    continue
                title = None
                if "title" in methods:
                    if meta[methods["title"]] is None:
                        logger.error("Metadata Error: title attribute is blank")
                    else:
                        title = meta[methods["title"]]
            else:
                logger.separator(f"{mapping_name} Metadata", space=False, border=False)
                logger.info("")
                year = None
                if "year" in methods and not self.library.is_music:
                    if meta[methods["year"]] is None:
                        raise Failed("Metadata Error: year attribute is blank")
                    try:
                        year_value = int(str(meta[methods["year"]]))
                        if 1800 <= year_value <= next_year:
                            year = year_value
                    except ValueError:
                        pass
                    if year is None:
                        raise Failed(f"Metadata Error: year attribute must be an integer between 1800 and {next_year}")

                edition_title = None
                if "edition_filter" in methods and self.library.is_movie:
                    edition_title = str(meta[methods["edition_filter"]])
                    if not edition_title:
                        edition_title = ""

                title = mapping_name
                if "title" in methods:
                    if meta[methods["title"]] is None:
                        logger.error("Metadata Error: title attribute is blank")
                    else:
                        title = meta[methods["title"]]

                item = self.library.search_item(title, year=year, edition=edition_title)

                if item is None and "alt_title" in methods:
                    if meta[methods["alt_title"]] is None:
                        logger.error("Metadata Error: alt_title attribute is blank")
                    else:
                        alt_title = meta[methods["alt_title"]]
                        item = self.library.search_item(alt_title, year=year, edition=edition_title)
                        if item is None:
                            item = self.library.search_item(alt_title, edition=edition_title)

                if item is None:
                    logger.error(f"Skipping {mapping_name}: Item {title} not found")
                    continue
            if not isinstance(item, list):
                item = [item]
            for i in item:
                self.update_metadata_item(i, title, mapping_name, meta, methods)

    def update_metadata_item(self, item, title, mapping_name, meta, methods):

        updated = False

        def add_edit(name, current_item, group=None, alias=None, key=None, value=None, var_type="str"):
            nonlocal updated
            if value or name in alias:
                if value or group[alias[name]]:
                    if key is None:         key = name
                    if value is None:       value = group[alias[name]]
                    try:
                        current = str(getattr(current_item, key, ""))
                        final_value = None
                        if var_type == "date":
                            final_value = util.validate_date(value, name, return_as="%Y-%m-%d")
                            current = current[:-9]
                        elif var_type == "float":
                            try:
                                value = float(str(value))
                                if 0 <= value <= 10:
                                    final_value = value
                            except ValueError:
                                pass
                            if final_value is None:
                                raise Failed(f"Metadata Error: {name} attribute must be a number between 0 and 10")
                        elif var_type == "int":
                            try:
                                final_value = int(str(value))
                            except ValueError:
                                pass
                            if final_value is None:
                                raise Failed(f"Metadata Error: {name} attribute must be an integer")
                        else:
                            final_value = value
                        if current != str(final_value):
                            if key == "title":
                                current_item.editTitle(final_value)
                            else:
                                current_item.editField(key, final_value)
                            logger.info(f"Detail: {name} updated to {final_value}")
                            updated = True
                    except Failed as ee:
                        logger.error(ee)
                else:
                    logger.error(f"Metadata Error: {name} attribute is blank")

        def finish_edit(current_item, description):
            nonlocal updated
            if updated:
                try:
                    current_item.saveEdits()
                    logger.info(f"{description} Details Update Successful")
                except BadRequest:
                    logger.error(f"{description} Details Update Failed")

        tmdb_item = None
        tmdb_is_movie = None
        if not self.library.is_music and ("tmdb_show" in methods or "tmdb_id" in methods) and "tmdb_movie" in methods:
            logger.error("Metadata Error: Cannot use tmdb_movie and tmdb_show when editing the same metadata item")

        if not self.library.is_music and "tmdb_show" in methods or "tmdb_id" in methods or "tmdb_movie" in methods:
            try:
                if "tmdb_show" in methods or "tmdb_id" in methods:
                    data = meta[methods["tmdb_show" if "tmdb_show" in methods else "tmdb_id"]]
                    if data is None:
                        logger.error("Metadata Error: tmdb_show attribute is blank")
                    else:
                        tmdb_is_movie = False
                        tmdb_item = self.config.TMDb.get_show(util.regex_first_int(data, "Show"))
                elif "tmdb_movie" in methods:
                    if meta[methods["tmdb_movie"]] is None:
                        logger.error("Metadata Error: tmdb_movie attribute is blank")
                    else:
                        tmdb_is_movie = True
                        tmdb_item = self.config.TMDb.get_movie(util.regex_first_int(meta[methods["tmdb_movie"]], "Movie"))
            except Failed as e:
                logger.error(e)

        originally_available = None
        original_title = None
        rating = None
        studio = None
        tagline = None
        summary = None
        genres = []
        if tmdb_item:
            originally_available = datetime.strftime(tmdb_item.release_date if tmdb_is_movie else tmdb_item.first_air_date, "%Y-%m-%d")

            if tmdb_item.original_title != tmdb_item.title:
                original_title = tmdb_item.original_title
            rating = tmdb_item.vote_average
            studio = tmdb_item.studio
            tagline = tmdb_item.tagline if len(tmdb_item.tagline) > 0 else None
            summary = tmdb_item.overview
            genres = tmdb_item.genres

        item.batchEdits()
        if title:
            add_edit("title", item, meta, methods, value=title)
        add_edit("sort_title", item, meta, methods, key="titleSort")
        if self.library.is_movie:
            add_edit("edition", item, meta, methods, key="editionTitle")
        add_edit("user_rating", item, meta, methods, key="userRating", var_type="float")
        if not self.library.is_music:
            add_edit("originally_available", item, meta, methods, key="originallyAvailableAt", value=originally_available, var_type="date")
            add_edit("critic_rating", item, meta, methods, value=rating, key="rating", var_type="float")
            add_edit("audience_rating", item, meta, methods, key="audienceRating", var_type="float")
            add_edit("content_rating", item, meta, methods, key="contentRating")
            add_edit("original_title", item, meta, methods, key="originalTitle", value=original_title)
            add_edit("studio", item, meta, methods, value=studio)
            add_edit("tagline", item, meta, methods, value=tagline)
        add_edit("summary", item, meta, methods, value=summary)
        for tag_edit in util.tags_to_edit[self.library.type]:
            if self.edit_tags(tag_edit, item, meta, methods, extra=genres if tag_edit == "genre" else None):
                updated = True
        finish_edit(item, f"{self.library.type}: {mapping_name}")

        if self.library.type in util.advance_tags_to_edit:
            advance_edits = {}
            prefs = [p.id for p in item.preferences()]
            for advance_edit in util.advance_tags_to_edit[self.library.type]:
                if advance_edit in methods:
                    if advance_edit in ["metadata_language", "use_original_title"] and self.library.agent not in plex.new_plex_agents:
                        logger.error(f"Metadata Error: {advance_edit} attribute only works for with the New Plex Movie Agent and New Plex TV Agent")
                    elif meta[methods[advance_edit]]:
                        ad_key, options = plex.item_advance_keys[f"item_{advance_edit}"]
                        method_data = str(meta[methods[advance_edit]]).lower()
                        if method_data not in options:
                            logger.error(f"Metadata Error: {meta[methods[advance_edit]]} {advance_edit} attribute invalid")
                        elif ad_key in prefs and getattr(item, ad_key) != options[method_data]:
                            advance_edits[ad_key] = options[method_data]
                            logger.info(f"Detail: {advance_edit} updated to {method_data}")
                    else:
                        logger.error(f"Metadata Error: {advance_edit} attribute is blank")
            if advance_edits:
                if self.library.edit_advance(item, advance_edits):
                    updated = True
                    logger.info(f"{mapping_name} Advanced Details Update Successful")
                else:
                    logger.error(f"{mapping_name} Advanced Details Update Failed")

        asset_location, folder_name, ups = self.library.item_images(item, meta, methods, initial=True, asset_directory=self.asset_directory + self.library.asset_directory if self.asset_directory else None)
        if ups:
            updated = True
        logger.info(f"{self.library.type}: {mapping_name} Details Update {'Complete' if updated else 'Not Needed'}")

        if "seasons" in methods and self.library.is_show:
            if not meta[methods["seasons"]]:
                logger.error("Metadata Error: seasons attribute is blank")
            elif not isinstance(meta[methods["seasons"]], dict):
                logger.error("Metadata Error: seasons attribute must be a dictionary")
            else:
                seasons = {}
                for season in item.seasons():
                    seasons[season.title] = season
                    seasons[int(season.index)] = season
                for season_id, season_dict in meta[methods["seasons"]].items():
                    updated = False
                    logger.info("")
                    logger.info(f"Updating season {season_id} of {mapping_name}...")
                    if season_id in seasons:
                        season = seasons[season_id]
                    else:
                        logger.error(f"Metadata Error: Season: {season_id} not found")
                        continue
                    season_methods = {sm.lower(): sm for sm in season_dict}
                    season.batchEdits()
                    add_edit("title", season, season_dict, season_methods)
                    add_edit("summary", season, season_dict, season_methods)
                    add_edit("user_rating", season, season_dict, season_methods, key="userRating", var_type="float")
                    if self.edit_tags("label", season, season_dict, season_methods):
                        updated = True
                    finish_edit(season, f"Season: {season_id}")
                    _, _, ups = self.library.item_images(season, season_dict, season_methods, asset_location=asset_location,
                                                         title=f"{item.title} Season {season.seasonNumber}",
                                                         image_name=f"Season{'0' if season.seasonNumber < 10 else ''}{season.seasonNumber}", folder_name=folder_name)
                    if ups:
                        updated = True
                    logger.info(f"Season {season_id} of {mapping_name} Details Update {'Complete' if updated else 'Not Needed'}")

                    if "episodes" in season_methods and self.library.is_show:
                        if not season_dict[season_methods["episodes"]]:
                            logger.error("Metadata Error: episodes attribute is blank")
                        elif not isinstance(season_dict[season_methods["episodes"]], dict):
                            logger.error("Metadata Error: episodes attribute must be a dictionary")
                        else:
                            episodes = {}
                            for episode in season.episodes():
                                episodes[episode.title] = episode
                                episodes[int(episode.index)] = episode
                            for episode_str, episode_dict in season_dict[season_methods["episodes"]].items():
                                updated = False
                                logger.info("")
                                logger.info(f"Updating episode {episode_str} in {season_id} of {mapping_name}...")
                                if episode_str in episodes:
                                    episode = episodes[episode_str]
                                else:
                                    logger.error(f"Metadata Error: Episode {episode_str} in Season {season_id} not found")
                                    continue
                                episode_methods = {em.lower(): em for em in episode_dict}
                                episode.batchEdits()
                                add_edit("title", episode, episode_dict, episode_methods)
                                add_edit("sort_title", episode, episode_dict, episode_methods, key="titleSort")
                                add_edit("content_rating", episode, episode_dict, episode_methods, key="contentRating")
                                add_edit("critic_rating", episode, episode_dict, episode_methods, key="rating", var_type="float")
                                add_edit("audience_rating", episode, episode_dict, episode_methods, key="audienceRating", var_type="float")
                                add_edit("user_rating", episode, episode_dict, episode_methods, key="userRating", var_type="float")
                                add_edit("originally_available", episode, episode_dict, episode_methods, key="originallyAvailableAt", var_type="date")
                                add_edit("summary", episode, episode_dict, episode_methods)
                                for tag_edit in ["director", "writer", "label"]:
                                    if self.edit_tags(tag_edit, episode, episode_dict, episode_methods):
                                        updated = True
                                finish_edit(episode, f"Episode: {episode_str} in Season: {season_id}")
                                _, _, ups = self.library.item_images(episode, episode_dict, episode_methods, asset_location=asset_location,
                                                                     title=f"{item.title} {episode.seasonEpisode.upper()}",
                                                                     image_name=episode.seasonEpisode.upper(), folder_name=folder_name)
                                if ups:
                                    updated = True
                                logger.info(f"Episode {episode_str} in Season {season_id} of {mapping_name} Details Update {'Complete' if updated else 'Not Needed'}")

        if "episodes" in methods and self.library.is_show:
            if not meta[methods["episodes"]]:
                logger.error("Metadata Error: episodes attribute is blank")
            elif not isinstance(meta[methods["episodes"]], dict):
                logger.error("Metadata Error: episodes attribute must be a dictionary")
            else:
                for episode_str, episode_dict in meta[methods["episodes"]].items():
                    updated = False
                    logger.info("")
                    match = re.search("[Ss]\\d+[Ee]\\d+", episode_str)
                    if not match:
                        logger.error(f"Metadata Error: episode {episode_str} invalid must have S##E## format")
                        continue
                    output = match.group(0)[1:].split("E" if "E" in match.group(0) else "e")
                    season_id = int(output[0])
                    episode_id = int(output[1])
                    logger.info(f"Updating episode S{season_id}E{episode_id} of {mapping_name}...")
                    try:
                        episode = item.episode(season=season_id, episode=episode_id)
                    except NotFound:
                        logger.error(f"Metadata Error: episode {episode_id} of season {season_id} not found")
                        continue
                    episode_methods = {em.lower(): em for em in episode_dict}
                    episode.batchEdits()
                    add_edit("title", episode, episode_dict, episode_methods)
                    add_edit("sort_title", episode, episode_dict, episode_methods, key="titleSort")
                    add_edit("content_rating", episode, episode_dict, episode_methods, key="contentRating")
                    add_edit("critic_rating", episode, episode_dict, episode_methods, key="rating", var_type="float")
                    add_edit("audience_rating", episode, episode_dict, episode_methods, key="audienceRating", var_type="float")
                    add_edit("user_rating", episode, episode_dict, episode_methods, key="userRating", var_type="float")
                    add_edit("originally_available", episode, episode_dict, episode_methods, key="originallyAvailableAt", var_type="date")
                    add_edit("summary", episode, episode_dict, episode_methods)
                    for tag_edit in ["director", "writer", "label"]:
                        if self.edit_tags(tag_edit, episode, episode_dict, episode_methods):
                            updated = True
                    finish_edit(episode, f"Episode: {episode_str} in Season: {season_id}")
                    _, _, ups = self.library.item_images(episode, episode_dict, episode_methods, asset_location=asset_location,
                                                         title=f"{item.title} {episode.seasonEpisode.upper()}",
                                                         image_name=episode.seasonEpisode.upper(), folder_name=folder_name)
                    if ups:
                        updated = True
                    logger.info(f"Episode S{season_id}E{episode_id} of {mapping_name} Details Update {'Complete' if updated else 'Not Needed'}")

        if "albums" in methods and self.library.is_music:
            if not meta[methods["albums"]]:
                logger.error("Metadata Error: albums attribute is blank")
            elif not isinstance(meta[methods["albums"]], dict):
                logger.error("Metadata Error: albums attribute must be a dictionary")
            else:
                albums = {album.title: album for album in item.albums()}
                for album_name, album_dict in meta[methods["albums"]].items():
                    updated = False
                    title = None
                    album_methods = {am.lower(): am for am in album_dict}
                    logger.info("")
                    logger.info(f"Updating album {album_name} of {mapping_name}...")
                    if album_name in albums:
                        album = albums[album_name]
                    elif "alt_title" in album_methods and album_dict[album_methods["alt_title"]] and album_dict[album_methods["alt_title"]] in albums:
                        album = albums[album_dict[album_methods["alt_title"]]]
                        title = album_name
                    else:
                        logger.error(f"Metadata Error: Album: {album_name} not found")
                        continue
                    if not title:
                        title = album.title
                    album.batchEdits()
                    add_edit("title", album, album_dict, album_methods, value=title)
                    add_edit("sort_title", album, album_dict, album_methods, key="titleSort")
                    add_edit("critic_rating", album, album_dict, album_methods, key="rating", var_type="float")
                    add_edit("user_rating", album, album_dict, album_methods, key="userRating", var_type="float")
                    add_edit("originally_available", album, album_dict, album_methods, key="originallyAvailableAt", var_type="date")
                    add_edit("record_label", album, album_dict, album_methods, key="studio")
                    add_edit("summary", album, album_dict, album_methods)
                    for tag_edit in ["genre", "style", "mood", "collection", "label"]:
                        if self.edit_tags(tag_edit, album, album_dict, album_methods):
                            updated = True
                    finish_edit(album, f"Album: {title}")
                    _, _, ups = self.library.item_images(album, album_dict, album_methods, asset_location=asset_location,
                                                         title=f"{item.title} Album {album.title}", image_name=album.title, folder_name=folder_name)
                    if ups:
                        updated = True
                    logger.info(f"Album: {title} of {mapping_name} Details Update {'Complete' if updated else 'Not Needed'}")

                    if "tracks" in album_methods:
                        if not album_dict[album_methods["tracks"]]:
                            logger.error("Metadata Error: tracks attribute is blank")
                        elif not isinstance(album_dict[album_methods["tracks"]], dict):
                            logger.error("Metadata Error: tracks attribute must be a dictionary")
                        else:
                            tracks = {}
                            for track in album.tracks():
                                tracks[track.title] = track
                                tracks[int(track.index)] = track
                            for track_num, track_dict in album_dict[album_methods["tracks"]].items():
                                updated = False
                                title = None
                                track_methods = {tm.lower(): tm for tm in track_dict}
                                logger.info("")
                                logger.info(f"Updating track {track_num} on {album_name} of {mapping_name}...")
                                if track_num in tracks:
                                    track = tracks[track_num]
                                elif "alt_title" in track_methods and track_dict[track_methods["alt_title"]] and track_dict[track_methods["alt_title"]] in tracks:
                                    track = tracks[track_dict[track_methods["alt_title"]]]
                                    title = track_num
                                else:
                                    logger.error(f"Metadata Error: Track: {track_num} not found")
                                    continue

                                if not title:
                                    title = track.title
                                track.batchEdits()
                                add_edit("title", track, track_dict, track_methods, value=title)
                                add_edit("user_rating", track, track_dict, track_methods, key="userRating", var_type="float")
                                add_edit("track", track, track_dict, track_methods, key="index", var_type="int")
                                add_edit("disc", track, track_dict, track_methods, key="parentIndex", var_type="int")
                                add_edit("original_artist", track, track_dict, track_methods, key="originalTitle")
                                for tag_edit in ["mood", "collection", "label"]:
                                    if self.edit_tags(tag_edit, track, track_dict, track_methods):
                                        updated = True
                                finish_edit(track, f"Track: {title}")
                                logger.info(f"Track: {track_num} on Album: {title} of {mapping_name} Details Update {'Complete' if updated else 'Not Needed'}")

        if "f1_season" in methods and self.library.is_show:
            f1_season = None
            current_year = datetime.now().year
            if meta[methods["f1_season"]] is None:
                raise Failed("Metadata Error: f1_season attribute is blank")
            try:
                year_value = int(str(meta[methods["f1_season"]]))
                if 1950 <= year_value <= current_year:
                    f1_season = year_value
            except ValueError:
                pass
            if f1_season is None:
                raise Failed(f"Metadata Error: f1_season attribute must be an integer between 1950 and {current_year}")
            round_prefix = False
            if "round_prefix" in methods:
                if meta[methods["round_prefix"]] is True:
                    round_prefix = True
                else:
                    logger.error("Metadata Error: round_prefix must be true to do anything")
            shorten_gp = False
            if "shorten_gp" in methods:
                if meta[methods["shorten_gp"]] is True:
                    shorten_gp = True
                else:
                    logger.error("Metadata Error: shorten_gp must be true to do anything")
            f1_language = None
            if "f1_language" in methods:
                if str(meta[methods["f1_language"]]).lower() in ergast.translations:
                    f1_language = str(meta[methods["f1_language"]]).lower()
                else:
                    logger.error(f"Metadata Error: f1_language must be a language code PMM has a translation for. Options: {ergast.translations}")
            logger.info(f"Setting Metadata of {item.title} to F1 Season {f1_season}")
            races = self.config.Ergast.get_races(f1_season, f1_language)
            race_lookup = {r.round: r for r in races}
            for season in item.seasons():
                if not season.seasonNumber:
                    continue
                sprint_weekend = False
                for episode in season.episodes():
                    if "sprint" in episode.locations[0].lower():
                        sprint_weekend = True
                        break
                if season.seasonNumber in race_lookup:
                    race = race_lookup[season.seasonNumber]
                    title = race.format_name(round_prefix, shorten_gp)
                    updated = False
                    season.batchEdits()
                    add_edit("title", season, value=title)
                    finish_edit(season, f"Season: {title}")
                    _, _, ups = self.library.item_images(season, {}, {}, asset_location=asset_location, title=title,
                                                         image_name=f"Season{'0' if season.seasonNumber < 10 else ''}{season.seasonNumber}", folder_name=folder_name)
                    if ups:
                        updated = True
                    logger.info(f"Race {season.seasonNumber} of F1 Season {f1_season}: Details Update {'Complete' if updated else 'Not Needed'}")
                    for episode in season.episodes():
                        if len(episode.locations) > 0:
                            ep_title, session_date = race.session_info(episode.locations[0], sprint_weekend)
                            episode.batchEdits()
                            add_edit("title", episode, value=ep_title)
                            add_edit("originally_available", episode, key="originallyAvailableAt", var_type="date", value=session_date)
                            finish_edit(episode, f"Season: {season.seasonNumber} Episode: {episode.episodeNumber}")
                            _, _, ups = self.library.item_images(episode, {}, {}, asset_location=asset_location, title=ep_title,
                                                                 image_name=episode.seasonEpisode.upper(), folder_name=folder_name)
                            if ups:
                                updated = True
                            logger.info(f"Session {episode.title}: Details Update {'Complete' if updated else 'Not Needed'}")
                else:
                    logger.warning(f"Ergast Error: No Round: {season.seasonNumber} for Season {f1_season}")

class PlaylistFile(DataFile):
    def __init__(self, config, file_type, path, temp_vars, asset_directory):
        super().__init__(config, file_type, path, temp_vars, asset_directory)
        self.data_type = "Playlist"
        logger.info("")
        logger.info(f"Loading Playlist {file_type}: {path}")
        logger.debug("")
        data = self.load_file(self.type, self.path)
        self.playlists = get_dict("playlists", data, self.config.playlist_names)
        self.templates = get_dict("templates", data)
        self.external_templates(data)
        self.translation_files(data)
        if not self.playlists:
            raise Failed("YAML Error: playlists attribute is required")
        logger.info(f"Playlist File Loaded Successfully")

class OverlayFile(DataFile):
    def __init__(self, config, library, file_type, path, temp_vars, asset_directory, queue_current):
        super().__init__(config, file_type, path, temp_vars, asset_directory)
        self.library = library
        self.data_type = "Overlay"
        logger.info("")
        logger.info(f"Loading Overlay {file_type}: {path}")
        logger.debug("")
        data = self.load_file(self.type, self.path, overlay=True)
        self.overlays = get_dict("overlays", data)
        self.templates = get_dict("templates", data)
        queues = get_dict("queues", data)
        self.queues = {}
        self.queue_names = {}
        position = temp_vars["position"] if "position" in temp_vars and temp_vars["position"] else None
        overlay_limit = util.parse("Config", "overlay_limit", temp_vars["overlay_limit"], datatype="int", default=0, minimum=0) if "overlay_limit" in temp_vars else None
        for queue_name, queue in queues.items():
            queue_position = temp_vars[f"position_{queue_name}"] if f"position_{queue_name}" in temp_vars and temp_vars[f"position_{queue_name}"] else position
            initial_queue = None
            defaults = {"horizontal_align": None, "vertical_align": None, "horizontal_offset": None, "vertical_offset": None}
            if isinstance(queue, dict) and "default" in queue and queue["default"] and isinstance(queue["default"], dict):
                for k, v in queue["default"].items():
                    if k == "position":
                        if not queue_position:
                            queue_position = v
                    elif k == "overlay_limit":
                        if overlay_limit is None:
                            overlay_limit = util.parse("Config", "overlay_limit", v, datatype="int", default=0, minimum=0)
                    elif k == "conditionals":
                        if not v:
                            raise Failed(f"Queue Error: default sub-attribute conditionals is blank")
                        if not isinstance(v, dict):
                            raise Failed(f"Queue Error: default sub-attribute conditionals is not a dictionary")
                        for con_key, con_value in v.items():
                            if not isinstance(con_value, dict):
                                raise Failed(f"Queue Error: conditional {con_key} is not a dictionary")
                            if "default" not in con_value:
                                raise Failed(f"Queue Error: default sub-attribute required for conditional {con_key}")
                            if "conditions" not in con_value:
                                raise Failed(f"Queue Error: conditions sub-attribute required for conditional {con_key}")
                            conditions = con_value["conditions"]
                            if isinstance(conditions, dict):
                                conditions = [conditions]
                            if not isinstance(conditions, list):
                                raise Failed(f"{self.data_type} Error: conditions sub-attribute must be a list or dictionary")
                            condition_found = False
                            for i, condition in enumerate(conditions, 1):
                                if not isinstance(condition, dict):
                                    raise Failed(f"{self.data_type} Error: each condition must be a dictionary")
                                if "value" not in condition:
                                    raise Failed(f"{self.data_type} Error: each condition must have a result value")
                                condition_passed = True
                                for var_key, var_value in condition.items():
                                    if var_key == "value":
                                        continue
                                    if var_key.endswith(".exists"):
                                        var_value = util.parse(self.data_type, var_key, var_value, datatype="bool", default=False)
                                        if (not var_value and var_key[:-7] in temp_vars and temp_vars[var_key[:-7]]) or (var_value and (var_key[:-7] not in temp_vars or not temp_vars[var_key[:-7]])):
                                            logger.debug(f"Condition {i} Failed: {var_key}: {'true does not exist' if var_value else 'false exists'}")
                                            condition_passed = False
                                    elif var_key.endswith(".not"):
                                        if (isinstance(var_value, list) and temp_vars[var_key] in var_value) or \
                                                (not isinstance(var_value, list) and str(temp_vars[var_key]) == str(var_value)):
                                            if isinstance(var_value, list):
                                                logger.debug(f'Condition {i} Failed: {var_key} "{temp_vars[var_key]}" in {var_value}')
                                            else:
                                                logger.debug(f'Condition {i} Failed: {var_key} "{temp_vars[var_key]}" is "{var_value}"')
                                            condition_passed = False
                                    elif var_key in temp_vars:
                                        if (isinstance(var_value, list) and temp_vars[var_key] not in var_value) or \
                                                (not isinstance(var_value, list) and str(temp_vars[var_key]) != str(var_value)):
                                            if isinstance(var_value, list):
                                                logger.debug(f'Condition {i} Failed: {var_key} "{temp_vars[var_key]}" not in {var_value}')
                                            else:
                                                logger.debug(f'Condition {i} Failed: {var_key} "{temp_vars[var_key]}" is not "{var_value}"')
                                            condition_passed = False
                                    else:
                                        logger.debug(f"Condition {i} Failed: {var_key} is not a variable provided or a default variable")
                                        condition_passed = False
                                    if condition_passed is False:
                                        break
                                if condition_passed:
                                    condition_found = True
                                    defaults[con_key] = condition["value"]
                                    break
                            if not condition_found:
                                defaults[con_key] = con_value["default"]
                    else:
                        defaults[k] = v
            if queue_position and isinstance(queue_position, list):
                initial_queue = queue_position
            elif isinstance(queue, list):
                initial_queue = queue
            elif isinstance(queue, dict):
                if queue_position:
                    pos_str = str(queue_position)
                    for x in range(4):
                        dict_to_use = temp_vars if x < 2 else defaults
                        for k, v in dict_to_use.items():
                            if f"<<{k}>>" in pos_str:
                                pos_str = pos_str.replace(f"<<{k}>>", str(v))
                    if pos_str in queue:
                        initial_queue = queue[pos_str]
                if not initial_queue:
                    initial_queue = next((v for k, v in queue.items() if k != "default"), None)
            if not isinstance(initial_queue, list):
                raise Failed(f"Config Error: queue {queue_name} must be a list")
            final_queue = []
            for pos in initial_queue:
                if not pos:
                    pos = {}
                defaults["horizontal_align"] = pos["horizontal_align"] if "horizontal_align" in pos else defaults["horizontal_align"]
                defaults["vertical_align"] = pos["vertical_align"] if "vertical_align" in pos else defaults["vertical_align"]
                defaults["horizontal_offset"] = pos["horizontal_offset"] if "horizontal_offset" in pos else defaults["horizontal_offset"]
                defaults["vertical_offset"] = pos["vertical_offset"] if "vertical_offset" in pos else defaults["vertical_offset"]
                new_pos = {
                    "horizontal_align": defaults["horizontal_align"], "vertical_align": defaults["vertical_align"],
                    "horizontal_offset": defaults["horizontal_offset"], "vertical_offset": defaults["vertical_offset"]
                }
                for pk, pv in new_pos.items():
                    if pv is None:
                        raise Failed(f"Config Error: queue missing {pv} attribute")
                final_queue.append(util.parse_cords(new_pos, f"{queue_name} queue", required=True))
                if overlay_limit and len(final_queue) >= overlay_limit:
                    break
            self.queues[queue_current] = final_queue
            self.queue_names[queue_name] = queue_current
            queue_current += 1
        self.external_templates(data, overlay=True)
        self.translation_files(data, overlay=True)
        if not self.overlays:
            raise Failed("YAML Error: overlays attribute is required")
        logger.info(f"Overlay File Loaded Successfully")
