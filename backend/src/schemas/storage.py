from pydantic import BaseModel, Field
class MigrationInput(BaseModel):
    new_dir: str = Field(default='', max_length=255)
