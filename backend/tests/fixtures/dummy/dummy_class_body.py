class Config:
    DEBUG = True
    MAX_RETRIES = 3

    class Inner:
        pass

    def method(self):
        return self.DEBUG
