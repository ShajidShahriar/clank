def my_decorator(func):
    return func


@my_decorator
def top_level_func():
    return 1


class Foo:
    @staticmethod
    def static_method():
        return 2

    @property
    def prop(self):
        return 3

    def normal_method(self):
        return 4
