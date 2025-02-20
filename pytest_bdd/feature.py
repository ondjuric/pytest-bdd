"""Feature.

The way of describing the behavior is based on Gherkin language, but a very
limited version. It doesn't support any parameter tables.
If the parametrization is needed to generate more test cases it can be done
on the fixture level of the pytest.
The <variable> syntax can be used here to make a connection between steps and
it will also validate the parameters mentioned in the steps with ones
provided in the pytest parametrization table.

Syntax example:

    Scenario: Publishing the article
        Given I'm an author user
        And I have an article
        When I go to the article page
        And I press the publish button
        Then I should not see the error message
        And the article should be published  # Note: will query the database

:note: The "#" symbol is used for comments.
:note: There're no multiline steps, the description of the step must fit in
one line.
"""

from collections import OrderedDict
from os import path as op
import codecs
import re
import sys
import textwrap

import glob2
import six

from . import types
from . import exceptions


# Global features dictionary
features = {}


STEP_PREFIXES = [
    ("Feature: ", types.FEATURE),
    ("Scenario Outline: ", types.SCENARIO_OUTLINE),
    ("Examples: Vertical", types.EXAMPLES_VERTICAL),
    ("Examples:", types.EXAMPLES),
    ("Scenario: ", types.SCENARIO),
    ("Background:", types.BACKGROUND),
    ("Given ", types.GIVEN),
    ("When ", types.WHEN),
    ("Then ", types.THEN),
    ("@", types.TAG),
    ("And ", None),  # Unknown step type,
]

STEP_PARAM_RE = re.compile("\<(.+?)\>")
COMMENT_RE = re.compile('(^|(?<=\s))#')


def get_step_type(line):
    """Detect step type by the beginning of the line.

    :param str line: Line of the Feature file.

    :return: SCENARIO, GIVEN, WHEN, THEN, or `None` if can't be detected.
    """
    for prefix, _type in STEP_PREFIXES:
        if line.startswith(prefix):
            return _type


def strip_comments(line):
    """Remove comments.

    :param str line: Line of the Feature file.

    :return: Stripped line.
    """
    res = COMMENT_RE.search(line)
    if res:
        line = line[:res.start()]
    return line.strip()


def parse_line(line):
    """Parse step line to get the step prefix (Scenario, Given, When, Then or And) and the actual step name.

    :param line: Line of the Feature file.

    :return: `tuple` in form ("<prefix>", "<Line without the prefix>").
    """
    for prefix, _ in STEP_PREFIXES:
        if line.startswith(prefix):
            return prefix.strip(), line[len(prefix):].strip()
    return "", line


def force_unicode(obj, encoding="utf-8"):
    """Get the unicode string out of given object (python 2 and python 3).

    :param obj: An `object`, usually a string.

    :return: unicode string.
    """
    if sys.version_info < (3, 0):
        if isinstance(obj, str):
            return obj.decode(encoding)
        else:
            return unicode(obj)
    else:  # pragma: no cover
        return str(obj)


def force_encode(string, encoding="utf-8"):
    """Force string encoding (Python compatibility function).

    :param str string: A string value.
    :param str encoding: Encoding.

    :return: Encoded string.
    """
    if sys.version_info < (3, 0):
        if isinstance(string, unicode):
            string = string.encode(encoding)
    return string


def get_tags(line):
    """Get tags out of the given line.

    :param str line: Feature file text line.

    :return: List of tags.
    """
    return (
        set((tag[1:] for tag in line.split() if tag.startswith("@") and len(tag) > 1))
        if line else set()
    )


def get_features(paths, **kwargs):
    """Get features for given paths.

    :param list paths: `list` of paths (file or dirs)

    :return: `list` of `Feature` objects.
    """
    seen_names = set()
    features = []
    for path in paths:
        if path not in seen_names:
            seen_names.add(path)
            if op.isdir(path):
                features.extend(get_features(glob2.iglob(op.join(path, "**", "*.feature"))))
            else:
                base, name = op.split(path)
                feature = Feature.get_feature(base, name, **kwargs)
                features.append(feature)
    features.sort(key=lambda feature: feature.name or feature.filename)
    return features


class Examples(object):

    """Example table."""

    def __init__(self):
        """Initialize examples instance."""
        self.example_params = []
        self.examples = []
        self.vertical_examples = []
        self.line_number = None
        self.name = None

    def set_param_names(self, keys):
        """Set parameter names.

        :param names: `list` of `string` parameter names.
        """
        self.example_params = [str(key) for key in keys]

    def add_example(self, values):
        """Add example.

        :param values: `list` of `string` parameter values.
        """
        self.examples.append(values)

    def add_example_row(self, param, values):
        """Add example row.

        :param param: `str` parameter name
        :param values: `list` of `string` parameter values
        """
        if param in self.example_params:
            raise exceptions.ExamplesNotValidError(
                """Example rows should contain unique parameters. "{0}" appeared more than once""".format(
                    param,
                )
            )
        self.example_params.append(param)
        self.vertical_examples.append(values)

    def get_params(self, converters):
        """Get scenario pytest parametrization table.

        :param converters: `dict` of converter functions to convert parameter values
        """
        param_count = len(self.example_params)
        if self.vertical_examples and not self.examples:
            for value_index in range(len(self.vertical_examples[0])):
                example = []
                for param_index in range(param_count):
                    example.append(self.vertical_examples[param_index][value_index])
                self.examples.append(example)

        if self.examples:
            params = []
            for example in self.examples:
                for index, param in enumerate(self.example_params):
                    if converters and param in converters:
                        example[index] = converters[param](example[index])
                params.append(example)
            return [self.example_params, params]
        else:
            return []

    def __bool__(self):
        """Bool comparison."""
        return bool(self.vertical_examples or self.examples)

    if six.PY2:
        __nonzero__ = __bool__


class Feature(object):

    """Feature."""

    def __init__(self, basedir, filename, encoding="utf-8", strict_gherkin=True):
        """Parse the feature file.

        :param str basedir: Feature files base directory.
        :param str filename: Relative path to the feature file.
        :param str encoding: Feature file encoding (utf-8 by default).
        :param bool strict_gherkin: Flag whether it's a strictly gherkin scenario or not (e.g. it will validate correct
            gherkin language (given-when-then))
        """
        self.scenarios = OrderedDict()
        self.rel_filename = op.join(op.basename(basedir), filename)
        self.filename = filename = op.abspath(op.join(basedir, filename))
        self.line_number = 1
        self.name = None
        self.tags = set()
        self.examples = Examples()
        scenario = None
        mode = None
        prev_mode = None
        description = []
        step = None
        multiline_step = False
        prev_line = None
        self.background = None

        with codecs.open(filename, encoding=encoding) as f:
            content = force_unicode(f.read(), encoding)
            for line_number, line in enumerate(content.splitlines(), start=1):
                unindented_line = line.lstrip()
                line_indent = len(line) - len(unindented_line)
                if step and (step.indent < line_indent or ((not unindented_line) and multiline_step)):
                    multiline_step = True
                    # multiline step, so just add line and continue
                    step.add_line(line)
                    continue
                else:
                    step = None
                    multiline_step = False
                stripped_line = line.strip()
                clean_line = strip_comments(line)
                if not clean_line and (not prev_mode or prev_mode not in types.FEATURE):
                    continue
                mode = get_step_type(clean_line) or mode

                if strict_gherkin:
                    if (self.background and not scenario and mode not in (
                            types.SCENARIO, types.SCENARIO_OUTLINE, types.GIVEN, types.TAG)):
                        raise exceptions.FeatureError(
                            "Background section can only contain Given steps", line_number, clean_line, filename)

                    if mode == types.GIVEN and prev_mode not in (
                            types.GIVEN, types.SCENARIO, types.SCENARIO_OUTLINE, types.BACKGROUND):
                        raise exceptions.FeatureError(
                            "Given steps must be the first within the Scenario", line_number, clean_line, filename)

                    if mode == types.WHEN and prev_mode not in (
                            types.SCENARIO, types.SCENARIO_OUTLINE, types.GIVEN, types.WHEN):
                        raise exceptions.FeatureError(
                            "When steps must be the first or follow Given steps", line_number, clean_line, filename)

                    if not self.background and mode == types.THEN and prev_mode not in types.STEP_TYPES:
                        raise exceptions.FeatureError(
                            "Then steps must follow Given or When steps", line_number, clean_line, filename)

                if mode == types.FEATURE:
                    if prev_mode is None or prev_mode == types.TAG:
                        _, self.name = parse_line(clean_line)
                        self.line_number = line_number
                        self.tags = get_tags(prev_line)
                    elif prev_mode == types.FEATURE:
                        description.append(clean_line)
                    else:
                        raise exceptions.FeatureError(
                            "Multiple features are not allowed in a single feature file",
                            line_number, clean_line, filename)

                prev_mode = mode

                # Remove Feature, Given, When, Then, And
                keyword, parsed_line = parse_line(clean_line)
                if mode in [types.SCENARIO, types.SCENARIO_OUTLINE]:
                    tags = get_tags(prev_line)
                    self.scenarios[parsed_line] = scenario = Scenario(self, parsed_line, line_number, tags=tags)
                elif mode == types.BACKGROUND:
                    self.background = Background(
                        feature=self,
                        line_number=line_number,
                    )
                elif mode == types.EXAMPLES:
                    mode = types.EXAMPLES_HEADERS
                    (scenario or self).examples.line_number = line_number
                elif mode == types.EXAMPLES_VERTICAL:
                    mode = types.EXAMPLE_LINE_VERTICAL
                    (scenario or self).examples.line_number = line_number
                elif mode == types.EXAMPLES_HEADERS:
                    (scenario or self).examples.set_param_names(
                        [l.strip() for l in parsed_line.split("|")[1:-1] if l.strip()])
                    mode = types.EXAMPLE_LINE
                elif mode == types.EXAMPLE_LINE:
                    (scenario or self).examples.add_example([l.strip() for l in stripped_line.split("|")[1:-1]])
                elif mode == types.EXAMPLE_LINE_VERTICAL:
                    param_line_parts = [l.strip() for l in stripped_line.split("|")[1:-1]]
                    try:
                        (scenario or self).examples.add_example_row(param_line_parts[0], param_line_parts[1:])
                    except exceptions.ExamplesNotValidError as exc:
                        if scenario:
                            raise exceptions.FeatureError(
                                """Scenario has not valid examples. {0}""".format(
                                    exc.args[0]), line_number, clean_line, filename)
                        else:
                            raise exceptions.FeatureError(
                                """Feature has not valid examples. {0}""".format(
                                    exc.args[0]), line_number, clean_line, filename)
                elif mode and mode not in (types.FEATURE, types.TAG):
                    step = Step(
                        name=parsed_line,
                        type=mode,
                        indent=line_indent,
                        line_number=line_number,
                        keyword=keyword,
                    )
                    if self.background and (mode == types.GIVEN or not strict_gherkin) and not scenario:
                        target = self.background
                    else:
                        target = scenario
                    target.add_step(step)
                prev_line = clean_line

        self.description = u"\n".join(description).strip()

    @classmethod
    def get_feature(cls, base_path, filename, encoding="utf-8", strict_gherkin=True):
        """Get a feature by the filename.

        :param str base_path: Base feature directory.
        :param str filename: Filename of the feature file.
        :param str encoding: Feature file encoding.
        :param bool strict_gherkin: Flag whether it's a strictly gherkin scenario or not (e.g. it will validate correct
            gherkin language (given-when-then))

        :return: `Feature` instance from the parsed feature cache.

        :note: The features are parsed on the execution of the test and
               stored in the global variable cache to improve the performance
               when multiple scenarios are referencing the same file.
        """
        full_name = op.abspath(op.join(base_path, filename))
        feature = features.get(full_name)
        if not feature:
            feature = Feature(base_path, filename, encoding=encoding, strict_gherkin=strict_gherkin)
            features[full_name] = feature
        return feature


class Scenario(object):

    """Scenario."""

    def __init__(self, feature, name, line_number, example_converters=None, tags=None):
        """Scenario constructor.

        :param pytest_bdd.feature.Feature feature: Feature.
        :param str name: Scenario name.
        :param int line_number: Scenario line number.
        :param dict example_converters: Example table parameter converters.
        :param set tags: Set of tags.
        """
        self.feature = feature
        self.name = name
        self._steps = []
        self.examples = Examples()
        self.line_number = line_number
        self.example_converters = example_converters
        self.tags = tags or set()
        self.failed = False
        self.test_function = None

    def add_step(self, step):
        """Add step to the scenario.

        :param pytest_bdd.feature.Step step: Step.
        """
        step.scenario = self
        self._steps.append(step)

    @property
    def steps(self):
        """Get scenario steps including background steps.

        :return: List of steps.
        """
        result = []
        if self.feature.background:
            result.extend(self.feature.background.steps)
        result.extend(self._steps)
        return result

    @property
    def params(self):
        """Get parameter names.

        :return: Parameter names.
        :rtype: frozenset
        """
        return frozenset(sum((list(step.params) for step in self.steps), []))

    def get_example_params(self):
        """Get examples."""
        return set(self.examples.example_params + self.feature.examples.example_params)

    def get_params(self):
        """Get example params."""
        for examples in [self.feature.examples, self.examples]:
            yield examples.get_params(self.example_converters)

    def validate(self):
        """Validate the scenario.

        :raises ScenarioValidationError: when scenario is not valid
        """
        params = self.params
        example_params = self.get_example_params()
        if params and example_params and params != example_params:
            raise exceptions.ScenarioExamplesNotValidError(
                """Scenario "{0}" in the feature "{1}" has not valid examples. """
                """Set of step parameters {2} should match set of example values {3}.""".format(
                    self.name, self.feature.filename, sorted(params), sorted(example_params),
                )
            )


@six.python_2_unicode_compatible
class Step(object):

    """Step."""

    def __init__(self, name, type, indent, line_number, keyword):
        """Step constructor.

        :param str name: step name.
        :param str type: step type.
        :param int indent: step text indent.
        :param int line_number: line number.
        :param str keyword: step keyword.
        """
        self.name = name
        self.keyword = keyword
        self.lines = []
        self.indent = indent
        self.type = type
        self.line_number = line_number
        self.failed = False
        self.start = 0
        self.stop = 0
        self.scenario = None
        self.background = None

    def add_line(self, line):
        """Add line to the multiple step.

        :param str line: Line of text - the continuation of the step name.
        """
        self.lines.append(line)

    @property
    def name(self):
        """Get step name."""
        lines = [self._name] + ([textwrap.dedent("\n".join(self.lines))] if self.lines else [])
        return "\n".join(lines).strip()

    @name.setter
    def name(self, value):
        """Set step name."""
        self._name = value

    def __str__(self):
        """Full step name including the type."""
        return '{type} "{name}"'.format(type=self.type.capitalize(), name=self.name)

    @property
    def params(self):
        """Get step params."""
        return tuple(frozenset(STEP_PARAM_RE.findall(self.name)))


class Background(object):

    """Background."""

    def __init__(self, feature, line_number):
        """Background constructor.

        :param pytest_bdd.feature.Feature feature: Feature.
        :param int line_number: Line number.
        """
        self.feature = feature
        self.line_number = line_number
        self.steps = []

    def add_step(self, step):
        """Add step to the background."""
        step.background = self
        self.steps.append(step)
